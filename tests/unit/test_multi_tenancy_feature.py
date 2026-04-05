"""Feature tests for multi-tenancy foundation — Phase 79.

These tests cover the positive (happy-path) acceptance criteria for:
- T79.0b: shared/models subpackage and Alembic model discovery
- T79.1: Organization and User models (field validation, metadata registration)
- T79.2: TenantContext, get_current_user, router org_id filtering
- T79.4: Per-tenant privacy ledger, EPSILON_SPENT_TOTAL org_id label

All tests were written in the FEATURE RED phase, AFTER attack tests,
per CLAUDE.md Rule 22.

Integration tests (real PostgreSQL, migration round-trip, tenant isolation)
live in tests/integration/test_tenant_isolation.py (T79.3).

CONSTITUTION Priority 3: TDD — FEATURE RED phase
Phase: 79 — Multi-Tenancy Foundation
"""

from __future__ import annotations

import base64
import os
import time
from typing import Any

import jwt as pyjwt
import pytest

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_TEST_SECRET = (  # pragma: allowlist secret
    "unit-test-jwt-secret-key-long-enough-for-hs256-32chars+"
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
        VaultState.unseal(bytearray(b"test-multi-tenancy-feature"))
        yield
        VaultState.reset()
    except ImportError:
        yield


# ---------------------------------------------------------------------------
# Helper: build a JWT with multi-tenant claims
# ---------------------------------------------------------------------------


def _make_token(
    *,
    sub: str,
    org_id: str,
    role: str = "admin",
    secret: str = _TEST_SECRET,
    exp_offset: int = 3600,
) -> str:
    """Build a valid multi-tenant JWT.

    Args:
        sub: Subject (user_id).
        org_id: Organization UUID claim.
        role: Role claim.
        secret: HMAC signing secret.
        exp_offset: Expiry offset from now in seconds.

    Returns:
        Compact JWT string.
    """
    now = int(time.time())
    return pyjwt.encode(
        {
            "sub": sub,
            "org_id": org_id,
            "role": role,
            "iat": now,
            "exp": now + exp_offset,
            "scope": ["read", "write"],
        },
        secret,
        algorithm="HS256",
    )


# ---------------------------------------------------------------------------
# T79.0b — shared/models subpackage
# ---------------------------------------------------------------------------


def test_shared_models_subpackage_is_importable() -> None:
    """shared/models/__init__.py exists and is importable.

    T79.0b AC: Create src/synth_engine/shared/models/__init__.py.
    """
    from synth_engine.shared import models

    assert models.__package__ == "synth_engine.shared.models", (
        "shared/models/__init__.py must set __package__ correctly"
    )


def test_alembic_autogenerate_detects_shared_models() -> None:
    """Alembic discovers Organization and User models from shared/models/.

    T79.0b AC: Update alembic/env.py to discover models from shared/models/.
    The models must be in SQLModel.metadata after import.
    """
    # Import alembic env to trigger metadata registration
    import sqlmodel

    from synth_engine.shared import models as _shared_models  # noqa: F401
    from synth_engine.shared.models.organization import Organization  # noqa: F401
    from synth_engine.shared.models.user import User  # noqa: F401

    table_names = list(sqlmodel.SQLModel.metadata.tables.keys())
    assert "organizations" in table_names, (
        f"'organizations' table not found in SQLModel.metadata. Found: {table_names}"
    )
    assert "users" in table_names, (
        f"'users' table not found in SQLModel.metadata. Found: {table_names}"
    )


# ---------------------------------------------------------------------------
# T79.1 — Organization model
# ---------------------------------------------------------------------------


def test_organization_model_extends_base_model() -> None:
    """Organization extends BaseModel (UUID PK, auto-discovered by Alembic).

    T79.1 AC: Both models extend BaseModel.
    """
    from synth_engine.shared.db import BaseModel
    from synth_engine.shared.models.organization import Organization

    assert issubclass(Organization, BaseModel)


def test_organization_model_fields() -> None:
    """Organization model has id, name, created_at, settings fields.

    T79.1 AC: Organization model: id, name, created_at, settings (JSON).
    """
    import uuid

    from synth_engine.shared.models.organization import Organization

    org = Organization(name="Test Corp")
    # id is auto-generated UUID
    assert org.id is not None
    assert isinstance(org.id, uuid.UUID)
    assert org.name == "Test Corp"
    assert org.created_at is not None
    # settings is an optional JSON field
    assert org.settings is None or isinstance(org.settings, str)


def test_organization_default_uuid_is_reserved() -> None:
    """Default org UUID 00000000-0000-0000-0000-000000000000 is distinct from UUIDv4.

    T79.1 AC: Default org uses reserved UUID that cannot collide with UUIDv4.
    """
    import uuid

    from synth_engine.shared.models.organization import Organization

    # The reserved UUID is all-zeros — cannot be generated by uuid.uuid4()
    reserved = uuid.UUID(_DEFAULT_ORG_UUID)
    random_uuid = uuid.uuid4()
    assert reserved != random_uuid
    assert str(reserved) == _DEFAULT_ORG_UUID

    # Organization can be created with the reserved UUID
    org = Organization(
        id=reserved,
        name="Default Organization",
    )
    assert str(org.id) == _DEFAULT_ORG_UUID


def test_organization_registered_in_sqlmodel_metadata() -> None:
    """Organization table is registered in SQLModel.metadata.

    T79.1 AC: Both models extend BaseModel (auto-discovered by Alembic
    via BaseModel.metadata).
    """
    import sqlmodel

    from synth_engine.shared.models.organization import Organization  # noqa: F401

    assert "organizations" in sqlmodel.SQLModel.metadata.tables


# ---------------------------------------------------------------------------
# T79.1 — User model
# ---------------------------------------------------------------------------


def test_user_model_extends_base_model() -> None:
    """User extends BaseModel (UUID PK, auto-discovered by Alembic).

    T79.1 AC: Both models extend BaseModel.
    """
    from synth_engine.shared.db import BaseModel
    from synth_engine.shared.models.user import User

    assert issubclass(User, BaseModel)


def test_user_model_fields() -> None:
    """User model has id, org_id, email, role, created_at fields.

    T79.1 AC: User model: id, org_id (FK to Organization), email, role, created_at.
    """
    import uuid

    from synth_engine.shared.models.user import User

    user = User(
        org_id=uuid.UUID(_DEFAULT_ORG_UUID),
        email="operator@example.com",
        role="operator",
    )
    assert user.id is not None
    assert str(user.org_id) == _DEFAULT_ORG_UUID
    assert user.email == "operator@example.com"
    assert user.role == "operator"
    assert user.created_at is not None


def test_user_registered_in_sqlmodel_metadata() -> None:
    """User table is registered in SQLModel.metadata.

    T79.1 AC: Both models extend BaseModel (auto-discovered by Alembic
    via BaseModel.metadata).
    """
    import sqlmodel

    from synth_engine.shared.models.user import User  # noqa: F401

    assert "users" in sqlmodel.SQLModel.metadata.tables


def test_default_user_uuid_is_reserved() -> None:
    """Default user UUID 00000000-0000-0000-0000-000000000001 is distinct from UUIDv4.

    T79.1 AC: Default user uses reserved UUID that cannot collide with UUIDv4.
    """
    import uuid

    from synth_engine.shared.models.user import User

    reserved_user_id = uuid.UUID(_DEFAULT_USER_UUID)
    assert str(reserved_user_id) == _DEFAULT_USER_UUID
    # Reserved user can be created
    user = User(
        id=reserved_user_id,
        org_id=uuid.UUID(_DEFAULT_ORG_UUID),
        email="system@default.local",
        role="admin",
    )
    assert str(user.id) == _DEFAULT_USER_UUID


# ---------------------------------------------------------------------------
# T79.2 — TenantContext dataclass
# ---------------------------------------------------------------------------


def test_tenant_context_is_frozen_dataclass() -> None:
    """TenantContext is a frozen dataclass with org_id, user_id, role.

    T79.2 AC: get_current_operator replaced with get_current_user returning
    TenantContext with (org_id, user_id, role).
    """
    import dataclasses
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

    # Must be a dataclass
    assert dataclasses.is_dataclass(ctx)

    # Must be frozen — mutation must raise
    with pytest.raises((FrozenInstanceError, AttributeError)):
        ctx.org_id = _ORG_B_UUID  # type: ignore[misc]


def test_get_current_user_with_valid_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """get_current_user returns TenantContext with org_id from JWT.

    T79.2 AC: get_current_user dependency returns (org_id, user_id, role)
    from JWT-embedded claims.  Calls get_current_user directly with a mocked
    Request to avoid the from __future__ import annotations scope issue with
    nested FastAPI endpoint definitions.
    """
    monkeypatch.setenv("CONCLAVE_ENV", "development")
    monkeypatch.setenv("JWT_SECRET_KEY", _TEST_SECRET)
    monkeypatch.setenv("JWT_ALGORITHM", "HS256")
    from synth_engine.shared.settings import get_settings

    get_settings.cache_clear()

    from starlette.requests import Request

    from synth_engine.bootstrapper.dependencies.tenant import TenantContext, get_current_user

    token = _make_token(sub=_USER_A_UUID, org_id=_ORG_A_UUID, role="operator")

    # Build a minimal mock Request with just the Authorization header.
    # Direct call avoids from __future__ import annotations scope issue
    # with nested FastAPI endpoints using Annotated[..., Depends(...)].
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/test",
        "query_string": b"",
        "headers": [(b"authorization", f"Bearer {token}".encode())],
    }
    request = Request(scope)

    ctx = get_current_user(request)

    assert isinstance(ctx, TenantContext)
    assert ctx.org_id == _ORG_A_UUID, f"Expected org_id={_ORG_A_UUID}, got {ctx.org_id}"
    assert ctx.user_id == _USER_A_UUID, f"Expected user_id={_USER_A_UUID}, got {ctx.user_id}"
    assert ctx.role == "operator", f"Expected role=operator, got {ctx.role}"


def test_get_current_user_passthrough_sentinel_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pass-through mode returns default org/user UUIDs and role='admin'.

    T79.2 AC: get_current_user pass-through sentinel returns default org UUID +
    default user UUID.  Calls get_current_user directly with a minimal mocked
    Request (no Authorization header) to avoid TestClient + from __future__
    import annotations scope issues with nested Annotated[..., Depends(...)]
    endpoint definitions.
    """
    monkeypatch.setenv("CONCLAVE_ENV", "development")
    monkeypatch.setenv("JWT_SECRET_KEY", "")
    monkeypatch.setenv("JWT_ALGORITHM", "HS256")
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

    # No Authorization header — pass-through mode with empty JWT_SECRET_KEY
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
    assert ctx.org_id == DEFAULT_ORG_UUID, (
        f"Pass-through org_id must be default sentinel {DEFAULT_ORG_UUID}"
    )
    assert ctx.user_id == DEFAULT_USER_UUID, (
        f"Pass-through user_id must be default sentinel {DEFAULT_USER_UUID}"
    )
    assert ctx.role == DEFAULT_ROLE


def test_get_current_user_rejects_missing_org_id_claim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """get_current_user rejects JWT without org_id claim (raises 401 HTTPException).

    T79.2 AC: verify_token() requires org_id claim in multi-tenant mode.
    A legacy JWT (no org_id) must not be accepted.  Calls get_current_user
    directly with a mocked Request to avoid from __future__ import annotations
    scope issues with nested Annotated[..., Depends(...)] endpoint definitions.
    """
    monkeypatch.setenv("CONCLAVE_ENV", "development")
    monkeypatch.setenv("JWT_SECRET_KEY", _TEST_SECRET)
    monkeypatch.setenv("JWT_ALGORITHM", "HS256")
    from synth_engine.shared.settings import get_settings

    get_settings.cache_clear()

    from fastapi import HTTPException
    from starlette.requests import Request

    from synth_engine.bootstrapper.dependencies.tenant import get_current_user

    # Legacy token without org_id claim
    now = int(time.time())
    legacy_token = pyjwt.encode(
        {"sub": _USER_A_UUID, "iat": now, "exp": now + 3600, "scope": []},
        _TEST_SECRET,
        algorithm="HS256",
    )

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/test",
        "query_string": b"",
        "headers": [(b"authorization", f"Bearer {legacy_token}".encode())],
    }
    request = Request(scope)

    with pytest.raises(HTTPException) as exc_info:
        get_current_user(request)

    assert exc_info.value.status_code == 401


# ---------------------------------------------------------------------------
# T79.2 — Settings: per_org_max_connections and jwt_expiry_seconds validator
# ---------------------------------------------------------------------------


def test_settings_per_org_max_connections_field_exists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ConclaveSettings has per_org_max_connections field (default 5).

    T79.2 AC: shared/settings_models.py adds per_org_max_connections: int = 5.
    """
    monkeypatch.setenv("CONCLAVE_ENV", "development")
    monkeypatch.setenv("JWT_SECRET_KEY", _TEST_SECRET)
    from synth_engine.shared.settings import get_settings

    get_settings.cache_clear()

    settings = get_settings()
    assert hasattr(settings, "per_org_max_connections")
    assert settings.per_org_max_connections == 5


def test_jwt_expiry_seconds_le_900_validator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """jwt_expiry_seconds ≤ 900 when multi-tenant is enabled.

    T79.4 AC / CONFIG-01: Pydantic validator on jwt_expiry_seconds.
    """
    monkeypatch.setenv("JWT_SECRET_KEY", _TEST_SECRET)
    monkeypatch.setenv("JWT_ALGORITHM", "HS256")
    monkeypatch.setenv("CONCLAVE_MULTI_TENANT_ENABLED", "true")
    monkeypatch.setenv("JWT_EXPIRY_SECONDS", "901")

    from synth_engine.shared.settings import get_settings

    get_settings.cache_clear()

    import pydantic

    with pytest.raises((ValueError, pydantic.ValidationError)):
        get_settings()


def test_jwt_expiry_seconds_900_is_valid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """jwt_expiry_seconds = 900 is the upper boundary — must be accepted.

    T79.4 AC / CONFIG-01: Exactly 900 seconds is valid.
    """
    monkeypatch.setenv("CONCLAVE_ENV", "development")
    monkeypatch.setenv("JWT_SECRET_KEY", _TEST_SECRET)
    monkeypatch.setenv("JWT_ALGORITHM", "HS256")
    monkeypatch.setenv("CONCLAVE_MULTI_TENANT_ENABLED", "true")
    monkeypatch.setenv("JWT_EXPIRY_SECONDS", "900")

    from synth_engine.shared.settings import get_settings

    get_settings.cache_clear()

    settings = get_settings()
    assert settings.jwt_expiry_seconds == 900


# ---------------------------------------------------------------------------
# T79.2 — shared/db.py: get_org_semaphore
# ---------------------------------------------------------------------------


def test_get_org_semaphore_returns_asyncio_semaphore() -> None:
    """get_org_semaphore returns an asyncio.Semaphore keyed by org_id.

    T79.2 AC: Tenant-aware connection pooling via application semaphore.
    """
    import asyncio

    from synth_engine.shared.db import get_org_semaphore

    sem = get_org_semaphore(_ORG_A_UUID)
    assert isinstance(sem, asyncio.Semaphore)
    # Semaphore must have a positive value (default concurrency limit)
    assert sem._value > 0, "Semaphore must have positive initial value"


def test_get_org_semaphore_same_org_returns_same_semaphore() -> None:
    """Calling get_org_semaphore with the same org_id returns the same semaphore.

    Each org must have exactly one semaphore instance — calling twice
    for the same org must return the same object (singleton per org).
    """
    from synth_engine.shared.db import get_org_semaphore

    sem1 = get_org_semaphore(_ORG_A_UUID)
    sem2 = get_org_semaphore(_ORG_A_UUID)
    assert sem1 is sem2


def test_get_org_semaphore_different_orgs_different_semaphores() -> None:
    """Different org IDs get independent semaphores.

    Isolation: one org's semaphore exhaustion must not affect another org.
    """
    from synth_engine.shared.db import get_org_semaphore

    sem_a = get_org_semaphore(_ORG_A_UUID)
    sem_b = get_org_semaphore(_ORG_B_UUID)
    assert sem_a is not sem_b


# ---------------------------------------------------------------------------
# T79.2 — Connection model gains org_id FK
# ---------------------------------------------------------------------------


def test_connection_model_has_org_id_field() -> None:
    """Connection model gains org_id FK column.

    T79.2 AC: Connection model gains org_id FK.
    """
    from synth_engine.bootstrapper.schemas.connections import Connection

    conn = Connection(
        name="test-conn",
        host="db.example",
        port=5432,
        database="testdb",
        schema_name="public",
        org_id=_ORG_A_UUID,
    )
    assert conn.org_id == _ORG_A_UUID


def test_synthesis_job_model_has_org_id_field() -> None:
    """SynthesisJob model gains org_id FK column.

    T79.2 AC: Job model gains org_id FK.
    """
    from synth_engine.modules.synthesizer.jobs.job_models import SynthesisJob

    job = SynthesisJob(
        total_epochs=1,
        num_rows=10,
        table_name="test",
        parquet_path="/tmp/test.parquet",
        org_id=_ORG_A_UUID,
    )
    assert job.org_id == _ORG_A_UUID


def test_webhook_registration_has_org_id_field() -> None:
    """WebhookRegistration gains org_id FK column.

    T79.2 AC: WebhookRegistration gains org_id FK; limit enforcement scoped per-org.
    """
    from synth_engine.bootstrapper.schemas.webhooks import WebhookRegistration

    wh = WebhookRegistration(
        owner_id=_USER_A_UUID,
        callback_url="https://hook.example.com/deliver",
        signing_key="test-signing-key-123",
        org_id=_ORG_A_UUID,
    )
    assert wh.org_id == _ORG_A_UUID


# ---------------------------------------------------------------------------
# T79.4 — Per-tenant privacy ledger
# ---------------------------------------------------------------------------


def test_privacy_ledger_has_org_id_field() -> None:
    """PrivacyLedger gains org_id FK column.

    T79.4 AC: PrivacyLedger model gains org_id FK.
    """
    from decimal import Decimal

    from synth_engine.modules.privacy.ledger import PrivacyLedger

    ledger = PrivacyLedger(
        total_allocated_epsilon=Decimal("10.0"),
        org_id=_ORG_A_UUID,
    )
    assert ledger.org_id == _ORG_A_UUID


def test_privacy_transaction_has_org_id_field() -> None:
    """PrivacyTransaction gains org_id FK as defense-in-depth.

    T79.4 AC: PrivacyTransaction gains org_id FK as defense-in-depth
    (ATTACK-06 mitigation).
    """
    from decimal import Decimal

    from synth_engine.modules.privacy.ledger import PrivacyTransaction

    tx = PrivacyTransaction(
        ledger_id=1,
        job_id=42,
        epsilon_spent=Decimal("0.5"),
        org_id=_ORG_A_UUID,
    )
    assert tx.org_id == _ORG_A_UUID
    assert tx.ledger_id == 1


def test_epsilon_spent_total_has_org_id_label() -> None:
    """EPSILON_SPENT_TOTAL Prometheus counter has org_id label.

    T79.4 AC: EPSILON_SPENT_TOTAL counter updated with org_id label;
    cardinality is bounded by number of orgs.
    """
    from synth_engine.modules.privacy.accountant import EPSILON_SPENT_TOTAL

    # Verify org_id is in the label names
    assert "org_id" in EPSILON_SPENT_TOTAL._labelnames


def test_assumptions_doc_has_a014() -> None:
    """docs/ASSUMPTIONS.md contains A-014 entry for application-level tenant isolation.

    T79.4 AC: Update docs/ASSUMPTIONS.md with A-014.
    """
    import pathlib

    assumptions_path = pathlib.Path(__file__).parent.parent.parent / "docs" / "ASSUMPTIONS.md"
    assert assumptions_path.exists(), "docs/ASSUMPTIONS.md does not exist"

    content = assumptions_path.read_text()
    assert "A-014" in content, "docs/ASSUMPTIONS.md must contain A-014 entry"
    assert "application-level" in content.lower() or "org_id" in content, (
        "A-014 must document application-level tenant isolation assumption"
    )


# ---------------------------------------------------------------------------
# T79.0 — ADR-0065 existence
# ---------------------------------------------------------------------------


def test_adr_0065_exists() -> None:
    """ADR-0065 multi-tenant JWT identity document exists.

    T79.0 AC: docs/adr/ADR-0065-multi-tenant-jwt-identity.md must exist
    and supersede ADR-0040 and ADR-0062.
    """
    import pathlib

    adr_path = (
        pathlib.Path(__file__).parent.parent.parent
        / "docs"
        / "adr"
        / "ADR-0065-multi-tenant-jwt-identity.md"
    )
    assert adr_path.exists(), f"ADR-0065 not found at {adr_path}"

    content = adr_path.read_text()
    # Must reference both superseded ADRs
    assert "ADR-0040" in content, "ADR-0065 must reference ADR-0040 (supersedes)"
    assert "ADR-0062" in content, "ADR-0065 must reference ADR-0062 (supersedes)"


# ---------------------------------------------------------------------------
# T79.2 — webhooks _count_active_registrations scoped per-org
# ---------------------------------------------------------------------------


def test_count_active_registrations_function_accepts_org_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_count_active_registrations accepts org_id parameter (per-org scoping).

    T79.2 AC: WebhookRegistration limit enforcement scoped per-org.
    """
    import inspect

    from synth_engine.bootstrapper.routers.webhooks import _count_active_registrations

    sig = inspect.signature(_count_active_registrations)
    params = list(sig.parameters.keys())
    assert "org_id" in params, (
        f"_count_active_registrations must accept org_id parameter. Got: {params}"
    )


# ---------------------------------------------------------------------------
# T79.2 — StaleTask has org_id field
# ---------------------------------------------------------------------------


def test_stale_task_has_org_id_field() -> None:
    """StaleTask dataclass has org_id field for reaper audit events.

    T79.2 AC: OrphanTaskReaper audit events include correct org_id.
    """
    from synth_engine.shared.tasks.repository import StaleTask

    task = StaleTask(task_id=42, status="TRAINING", org_id=_ORG_A_UUID)
    assert task.task_id == 42
    assert task.status == "TRAINING"
    assert task.org_id == _ORG_A_UUID
