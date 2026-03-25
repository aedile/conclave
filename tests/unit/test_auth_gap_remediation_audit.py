"""Feature tests for audit event actor using JWT sub (ADR-D1).

Tests cover:
- /security/shred audit actor is JWT sub, not hardcoded string.
- /security/keys/rotate audit actor is JWT sub.
- /privacy/budget/refresh audit actor is JWT sub (not X-Operator-Id header).
- shred_job audit uses current_operator (JWT sub) from T39.2.

Split from test_auth_gap_remediation.py (T56.3).

CONSTITUTION Priority 0: Security — audit integrity
Task: ADR-D1 — Add Authentication to Settings, Security & Privacy Routers
"""

from __future__ import annotations

import os
import time
from decimal import Decimal
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
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
_OPERATOR_SUB = "test-operator-remediation"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_token(
    sub: str = _OPERATOR_SUB,
    secret: str = _TEST_SECRET,
    exp_offset: int = 3600,
) -> str:
    """Create a JWT token for testing.

    Args:
        sub: Subject claim value.
        secret: HMAC secret to sign with.
        exp_offset: Seconds from now for expiry (negative = already expired).

    Returns:
        Compact JWT string.
    """
    import jwt as pyjwt

    now = int(time.time())
    return pyjwt.encode(
        {
            "sub": sub,
            "iat": now,
            "exp": now + exp_offset,
            "scope": ["read", "write", "security:admin", "settings:write"],
        },
        secret,
        algorithm="HS256",
    )


def _make_security_app(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Build a test FastAPI app with the security router, auth configured.

    Args:
        monkeypatch: pytest monkeypatch for env var injection.

    Returns:
        FastAPI app instance.
    """
    from synth_engine.bootstrapper.dependencies.auth import get_current_operator
    from synth_engine.bootstrapper.errors import register_error_handlers
    from synth_engine.bootstrapper.main import create_app
    from synth_engine.bootstrapper.routers.security import router as security_router

    monkeypatch.setenv("JWT_SECRET_KEY", _TEST_SECRET)
    monkeypatch.setenv("JWT_ALGORITHM", "HS256")

    from synth_engine.shared.settings import get_settings

    get_settings.cache_clear()

    app = create_app()
    register_error_handlers(app)
    app.include_router(security_router)

    # Remove any override for get_current_operator so the real dependency is used
    app.dependency_overrides.pop(get_current_operator, None)
    return app


def _make_privacy_app(monkeypatch: pytest.MonkeyPatch) -> tuple[Any, Any]:
    """Build a test FastAPI app with the privacy router and seeded ledger, auth configured.

    Args:
        monkeypatch: pytest monkeypatch for env var injection.

    Returns:
        Tuple of (app, engine).
    """
    from sqlalchemy.pool import StaticPool

    from synth_engine.bootstrapper.dependencies.auth import get_current_operator
    from synth_engine.bootstrapper.dependencies.db import get_db_session
    from synth_engine.bootstrapper.errors import register_error_handlers
    from synth_engine.bootstrapper.main import create_app
    from synth_engine.bootstrapper.routers.privacy import router as privacy_router
    from synth_engine.modules.privacy.ledger import PrivacyLedger

    monkeypatch.setenv("JWT_SECRET_KEY", _TEST_SECRET)
    monkeypatch.setenv("JWT_ALGORITHM", "HS256")
    monkeypatch.setenv("AUDIT_KEY", os.urandom(32).hex())

    from synth_engine.shared.settings import get_settings

    get_settings.cache_clear()

    from synth_engine.shared.security.audit import reset_audit_logger

    reset_audit_logger()

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    with Session(engine) as session:
        ledger = PrivacyLedger(
            total_allocated_epsilon=Decimal("10.0"),
            total_spent_epsilon=Decimal("3.5"),
        )
        session.add(ledger)
        session.commit()

    app = create_app()
    register_error_handlers(app)
    app.include_router(privacy_router)

    def _override_session() -> Any:
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_db_session] = _override_session
    # Remove any override for get_current_operator so the real dependency is used
    app.dependency_overrides.pop(get_current_operator, None)
    return app, engine


def _common_patches() -> list[Any]:
    """Return common mock patches for vault-seal and licensing checks.

    Returns:
        List of patch context managers.
    """
    return [
        patch(
            "synth_engine.bootstrapper.dependencies.vault.VaultState.is_sealed",
            return_value=False,
        ),
        patch(
            "synth_engine.bootstrapper.dependencies.licensing.LicenseState.is_licensed",
            return_value=True,
        ),
    ]


class TestSecurityAuditUsesJwtSub:
    """Security endpoint audit events must use the JWT sub as actor, not hardcoded 'operator'."""

    @pytest.mark.asyncio
    async def test_shred_vault_audit_actor_is_jwt_sub(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """POST /security/shred must emit audit event with actor=JWT sub, not 'operator'."""
        app = _make_security_app(monkeypatch)
        token = _make_token(sub="security-admin-007")
        patches = _common_patches()

        mock_audit = MagicMock()
        mock_audit.log_event = MagicMock()

        with (
            patches[0],
            patches[1],
            patch(
                "synth_engine.bootstrapper.routers.security.get_audit_logger",
                return_value=mock_audit,
            ),
            patch("synth_engine.bootstrapper.routers.security.VaultState.seal"),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                await client.post(
                    "/security/shred",
                    headers={"Authorization": f"Bearer {token}"},
                )

        mock_audit.log_event.assert_called_once()
        call_kwargs = mock_audit.log_event.call_args.kwargs
        # Actor MUST be the JWT sub, not the hardcoded string "operator"
        assert call_kwargs["actor"] == "security-admin-007"
        assert call_kwargs["actor"] != "operator"

    @pytest.mark.asyncio
    async def test_rotate_keys_audit_actor_is_jwt_sub(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """POST /security/keys/rotate must emit audit event with actor=JWT sub."""
        app = _make_security_app(monkeypatch)
        token = _make_token(sub="key-rotation-admin")
        patches = _common_patches()

        mock_audit = MagicMock()
        mock_audit.log_event = MagicMock()

        with (
            patches[0],
            patches[1],
            patch(
                "synth_engine.bootstrapper.routers.security.get_audit_logger",
                return_value=mock_audit,
            ),
            # Vault must be unsealed for rotation to proceed past the gate
            patch(
                "synth_engine.bootstrapper.routers.security.VaultState.is_sealed",
                return_value=False,
            ),
            # Mock get_fernet and the rotation task to avoid real crypto/Huey
            patch("synth_engine.bootstrapper.routers.security.get_fernet") as mock_fernet,
            patch("synth_engine.bootstrapper.routers.security.rotate_ale_keys_task"),
        ):
            mock_fernet_instance = MagicMock()
            mock_fernet_instance.encrypt.return_value = b"wrapped_key_bytes"
            mock_fernet.return_value = mock_fernet_instance

            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                await client.post(
                    "/security/keys/rotate",
                    json={"new_passphrase": "new-secure-pass"},
                    headers={"Authorization": f"Bearer {token}"},
                )

        mock_audit.log_event.assert_called_once()
        call_kwargs = mock_audit.log_event.call_args.kwargs
        # Actor MUST be the JWT sub, not the hardcoded string "operator"
        assert call_kwargs["actor"] == "key-rotation-admin"
        assert call_kwargs["actor"] != "operator"


class TestPrivacyAuditUsesJwtSub:
    """Privacy budget refresh audit events must use JWT sub as actor, not X-Operator-Id header."""

    @pytest.mark.asyncio
    async def test_refresh_budget_audit_actor_is_jwt_sub_not_header(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """POST /privacy/budget/refresh must use JWT sub as audit actor, not X-Operator-Id."""
        app, engine = _make_privacy_app(monkeypatch)
        # JWT sub is "jwt-sub-operator", header has a different value
        token = _make_token(sub="jwt-sub-operator")
        patches = _common_patches()

        mock_audit = MagicMock()
        mock_audit.log_event = MagicMock()

        def _fake_reset(*, ledger_id: int, new_allocated_epsilon: Any) -> tuple[Any, Any]:
            with Session(engine) as s:
                from synth_engine.modules.privacy.ledger import PrivacyLedger

                ledger = s.get(PrivacyLedger, ledger_id)
                if ledger is None:
                    from sqlalchemy.exc import NoResultFound

                    raise NoResultFound
                ledger.total_spent_epsilon = Decimal("0.0")
                s.add(ledger)
                s.commit()
                s.refresh(ledger)
                return ledger.total_allocated_epsilon, ledger.total_spent_epsilon

        with (
            patches[0],
            patches[1],
            patch(
                "synth_engine.bootstrapper.routers.privacy.get_audit_logger",
                return_value=mock_audit,
            ),
            patch(
                "synth_engine.bootstrapper.routers.privacy._run_reset_budget",
                side_effect=_fake_reset,
            ),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                await client.post(
                    "/privacy/budget/refresh",
                    json={"justification": "JWT sub audit actor test"},
                    headers={
                        "Authorization": f"Bearer {token}",
                        "X-Operator-Id": "header-operator-different",
                    },
                )

        mock_audit.log_event.assert_called_once()
        call_kwargs = mock_audit.log_event.call_args.kwargs
        # Actor MUST be the JWT sub, not the X-Operator-Id header
        assert call_kwargs["actor"] == "jwt-sub-operator"
        assert call_kwargs["actor"] != "header-operator-different"
        assert call_kwargs["actor"] != "unknown-operator"


# ---------------------------------------------------------------------------
# QA-R2-002: shred_job audit event actor must be current_operator (JWT sub)
# ---------------------------------------------------------------------------


def _make_jobs_app_with_owned_job(
    monkeypatch: pytest.MonkeyPatch,
    owner_sub: str,
) -> tuple[Any, Any]:
    """Build a test FastAPI app with the jobs router and a job owned by ``owner_sub``.

    Args:
        monkeypatch: pytest monkeypatch for env var injection.
        owner_sub: JWT sub claim used as the job's owner_id.

    Returns:
        Tuple of (app, job_id) for use in test assertions.
    """
    from sqlalchemy.pool import StaticPool
    from sqlmodel import Session, SQLModel, create_engine

    from synth_engine.bootstrapper.dependencies.auth import get_current_operator
    from synth_engine.bootstrapper.dependencies.db import get_db_session
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
        job = SynthesisJob(
            table_name="customers",
            parquet_path="/tmp/customers.parquet",
            total_epochs=10,
            num_rows=100,
            status="COMPLETE",
            owner_id=owner_sub,
        )
        session.add(job)
        session.commit()
        session.refresh(job)
        job_id = job.id

    app = create_app()
    register_error_handlers(app)
    app.include_router(jobs_router)

    def _override_session() -> Any:
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_db_session] = _override_session
    # Real get_current_operator — JWT verification enforced
    app.dependency_overrides.pop(get_current_operator, None)
    return app, job_id


class TestJobsShredAuditUsesCurrentOperator:
    """DELETE /jobs/{id}/shred audit event actor must equal the JWT sub claim.

    Covers the RT-001 fix that changed ``actor="system/api"`` to
    ``actor=current_operator`` in ``shred_job`` (jobs.py).  The existing test
    only checked ``event_type``; this class also asserts the ``actor`` field.
    """

    @pytest.mark.asyncio
    async def test_shred_job_audit_actor_is_jwt_sub(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """DELETE /jobs/{id}/shred must emit ARTIFACT_SHREDDED with actor=JWT sub.

        Calls the shred endpoint with a valid JWT whose sub is
        ``"shred-operator-r2"``.  The job's owner_id is pre-set to the same
        value so the ownership check passes.  Asserts that the ``actor`` field
        in the emitted ``ARTIFACT_SHREDDED`` audit event equals the JWT sub,
        not a hardcoded string like ``"system/api"``.
        """
        shred_sub = "shred-operator-r2"
        app, job_id = _make_jobs_app_with_owned_job(monkeypatch, owner_sub=shred_sub)
        token = _make_token(sub=shred_sub)
        patches = _common_patches()

        mock_audit = MagicMock()
        mock_audit.log_event = MagicMock()

        with (
            patches[0],
            patches[1],
            patch("synth_engine.bootstrapper.routers.jobs.shred_artifacts"),
            patch(
                "synth_engine.bootstrapper.routers.jobs.get_audit_logger",
                return_value=mock_audit,
            ),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    f"/jobs/{job_id}/shred",
                    headers={"Authorization": f"Bearer {token}"},
                )

        assert response.status_code == 200
        mock_audit.log_event.assert_called_once()
        call_kwargs = mock_audit.log_event.call_args.kwargs
        assert call_kwargs["event_type"] == "ARTIFACT_SHREDDED"
        # Actor MUST be the JWT sub claim, not a hardcoded value like "system/api"
        assert call_kwargs["actor"] == shred_sub
        assert call_kwargs["actor"] != "system/api"
