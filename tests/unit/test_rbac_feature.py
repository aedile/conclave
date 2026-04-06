"""Feature tests for RBAC — Phase 80. RED phase.

Tests the positive/feature behaviors of the RBAC system:
- Role enum and permission matrix structure
- has_permission() pure function correctness
- require_permission() dependency factory behavior
- Admin user management CRUD operations
- Audit log endpoint behavior
- Token issuance with DB-resolved role
- Erasure semantics (admin-delegated)
- Single-tenant backward compatibility (role="admin" on token)
- Background task RBAC exemption documentation

Written in the FEATURE RED phase, AFTER attack tests, BEFORE implementation.

CONSTITUTION Priority 3: TDD — FEATURE RED phase
Phase: 80 — Role-Based Access Control
"""

from __future__ import annotations

import time
from typing import Any
from unittest.mock import MagicMock, patch

import jwt as pyjwt
import pytest
from fastapi import Depends, FastAPI
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
_ORG_A_UUID = "11111111-1111-1111-1111-111111111111"
_USER_ADMIN_UUID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
_USER_OPERATOR_UUID = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
_USER_VIEWER_UUID = "cccccccc-cccc-cccc-cccc-cccccccccccc"
_USER_AUDITOR_UUID = "dddddddd-dddd-dddd-dddd-dddddddddddd"


# ---------------------------------------------------------------------------
# Shared fixtures
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


def _make_token(
    *,
    sub: str,
    org_id: str,
    role: str = "admin",
    secret: str = _TEST_SECRET,
    exp_offset: int = 3600,
) -> str:
    """Create a signed JWT for testing.

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
# T80.1: Role enum and permission matrix structure
# ---------------------------------------------------------------------------


def test_role_enum_has_four_values() -> None:
    """Role enum contains exactly admin, operator, viewer, auditor.

    Verifies the Role enum structure from bootstrapper/dependencies/permissions.py.
    """
    from synth_engine.bootstrapper.dependencies.permissions import Role

    role_values = {r.value for r in Role}
    assert role_values == {"admin", "operator", "viewer", "auditor"}


def test_permission_matrix_has_twenty_permissions() -> None:
    """PERMISSION_MATRIX contains exactly 20 permission entries.

    Verifies the static frozen data structure has the complete permission set
    as defined in the developer brief.
    """
    from synth_engine.bootstrapper.dependencies.permissions import PERMISSION_MATRIX

    assert len(PERMISSION_MATRIX) == 20


def test_permission_matrix_is_frozen() -> None:
    """PERMISSION_MATRIX values are frozensets (immutable).

    Prevents runtime modification of the permission matrix.
    """
    from synth_engine.bootstrapper.dependencies.permissions import PERMISSION_MATRIX

    for permission, roles in PERMISSION_MATRIX.items():
        assert isinstance(roles, frozenset), (
            f"Permission {permission!r} roles value is not a frozenset: {type(roles)}"
        )


def test_permission_matrix_contains_known_permissions() -> None:
    """PERMISSION_MATRIX contains all 20 expected permission strings.

    Verifies the complete list from the developer brief and phase-80.md.
    """
    from synth_engine.bootstrapper.dependencies.permissions import PERMISSION_MATRIX

    expected = {
        "connections:create",
        "connections:read",
        "connections:delete",
        "jobs:create",
        "jobs:read",
        "jobs:cancel",
        "jobs:download",
        "jobs:shred",
        "jobs:legal-hold",
        "webhooks:write",
        "webhooks:read",
        "privacy:read",
        "privacy:reset",
        "compliance:erasure",
        "compliance:audit-read",
        "security:admin",
        "admin:users",
        "admin:settings",
        "settings:read",
        "settings:write",
    }
    assert set(PERMISSION_MATRIX.keys()) == expected


def test_admin_has_all_permissions() -> None:
    """Admin role has all 20 permissions.

    Admin is the highest-privilege role — must have access to every operation.
    """
    from synth_engine.bootstrapper.dependencies.permissions import PERMISSION_MATRIX, Role

    for permission, allowed_roles in PERMISSION_MATRIX.items():
        assert Role.admin in allowed_roles, (
            f"Admin missing permission: {permission!r}"
        )


def test_privacy_read_accessible_to_all_roles() -> None:
    """privacy:read permission is accessible to all 4 roles.

    Privacy budget visibility should be broadly available — all roles can
    see the current budget state but only admin can reset it.
    """
    from synth_engine.bootstrapper.dependencies.permissions import PERMISSION_MATRIX, Role

    allowed = PERMISSION_MATRIX["privacy:read"]
    assert Role.admin in allowed
    assert Role.operator in allowed
    assert Role.viewer in allowed
    assert Role.auditor in allowed


# ---------------------------------------------------------------------------
# T80.1: has_permission() pure function
# ---------------------------------------------------------------------------


def test_has_permission_returns_true_for_allowed_role() -> None:
    """has_permission returns True when role has the permission.

    Pure function test — no HTTP overhead. Specific value assertion.
    """
    from synth_engine.bootstrapper.dependencies.permissions import has_permission

    result = has_permission(role="admin", permission="jobs:create")
    assert result is True


def test_has_permission_returns_false_for_disallowed_role() -> None:
    """has_permission returns False when role lacks the permission.

    Pure function test. Specific value assertion.
    """
    from synth_engine.bootstrapper.dependencies.permissions import has_permission

    result = has_permission(role="viewer", permission="jobs:create")
    assert result is False


def test_has_permission_returns_false_for_unknown_permission() -> None:
    """has_permission returns False for an unknown permission string.

    Unknown permissions default to deny (fail-closed). Specific value assertion.
    """
    from synth_engine.bootstrapper.dependencies.permissions import has_permission

    result = has_permission(role="admin", permission="nonexistent:permission")
    assert result is False


def test_has_permission_returns_false_for_unknown_role() -> None:
    """has_permission returns False for an unknown role string.

    Unknown roles default to deny (fail-closed). Specific value assertion.
    """
    from synth_engine.bootstrapper.dependencies.permissions import has_permission

    result = has_permission(role="superadmin", permission="jobs:create")
    assert result is False


def test_has_permission_auditor_can_read_audit_log() -> None:
    """has_permission returns True for auditor + compliance:audit-read.

    Verifies auditor's core capability. Specific value assertion.
    """
    from synth_engine.bootstrapper.dependencies.permissions import has_permission

    result = has_permission(role="auditor", permission="compliance:audit-read")
    assert result is True


def test_has_permission_auditor_cannot_read_jobs() -> None:
    """has_permission returns False for auditor + jobs:read.

    Auditor is strictly limited to audit/compliance capabilities. Specific value assertion.
    """
    from synth_engine.bootstrapper.dependencies.permissions import has_permission

    result = has_permission(role="auditor", permission="jobs:read")
    assert result is False


# ---------------------------------------------------------------------------
# T80.2: require_permission() dependency
# ---------------------------------------------------------------------------


def _make_permissioned_app(monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    """Build a minimal FastAPI app for require_permission tests.

    Args:
        monkeypatch: pytest monkeypatch fixture.

    Returns:
        FastAPI app with a jobs:read gated endpoint.
    """
    monkeypatch.setenv("CONCLAVE_ENV", "development")
    monkeypatch.setenv("JWT_SECRET_KEY", _TEST_SECRET)
    monkeypatch.setenv("JWT_ALGORITHM", "HS256")
    from synth_engine.shared.settings import get_settings

    get_settings.cache_clear()

    app = FastAPI()
    from synth_engine.bootstrapper.dependencies.permissions import require_permission

    @app.get("/test/jobs")
    def _get_jobs(ctx: Any = Depends(require_permission("jobs:read"))) -> dict[str, str]:  # noqa: B008
        return {"org_id": ctx.org_id, "role": ctx.role}

    return app


def test_require_permission_returns_tenant_context_on_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """require_permission returns TenantContext when role has permission.

    Verifies the dependency factory injects TenantContext into handlers.
    Specific value assertion: org_id and role returned in response.
    """
    app = _make_permissioned_app(monkeypatch)
    client = TestClient(app, raise_server_exceptions=False)

    token = _make_token(sub=_USER_ADMIN_UUID, org_id=_ORG_A_UUID, role="admin")
    response = client.get(
        "/test/jobs", headers={"Authorization": f"Bearer {token}"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["org_id"] == _ORG_A_UUID
    assert body["role"] == "admin"


def test_require_permission_viewer_can_read_jobs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Viewer has jobs:read permission — returns 200.

    Positive test: viewer role is allowed to read jobs.
    Specific value assertion.
    """
    app = _make_permissioned_app(monkeypatch)
    client = TestClient(app, raise_server_exceptions=False)

    token = _make_token(sub=_USER_VIEWER_UUID, org_id=_ORG_A_UUID, role="viewer")
    response = client.get(
        "/test/jobs", headers={"Authorization": f"Bearer {token}"}
    )

    assert response.status_code == 200
    assert response.json()["role"] == "viewer"


def test_require_permission_operator_can_read_jobs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Operator has jobs:read permission — returns 200.

    Positive test: operator role is allowed to read jobs. Specific value assertion.
    """
    app = _make_permissioned_app(monkeypatch)
    client = TestClient(app, raise_server_exceptions=False)

    token = _make_token(sub=_USER_OPERATOR_UUID, org_id=_ORG_A_UUID, role="operator")
    response = client.get(
        "/test/jobs", headers={"Authorization": f"Bearer {token}"}
    )

    assert response.status_code == 200
    assert response.json()["role"] == "operator"


def test_require_permission_403_detail_is_specific() -> None:
    """require_permission 403 response body contains specific detail text.

    Security requirement: error message must say 'Insufficient permissions',
    not an internal detail that could leak role/permission information.
    Specific value assertion: exact message text.
    """
    from synth_engine.bootstrapper.dependencies.permissions import (
        RBAC_403_DETAIL,
    )

    assert RBAC_403_DETAIL == "Insufficient permissions"


def test_require_permission_pass_through_mode_defaults_to_admin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pass-through mode (no JWT secret) uses admin role.

    Backward compatibility: existing single-tenant deployments without JWT
    configuration should have full access (admin role sentinel).
    """
    monkeypatch.setenv("CONCLAVE_ENV", "development")
    monkeypatch.setenv("JWT_SECRET_KEY", "")
    monkeypatch.setenv("CONCLAVE_PASS_THROUGH_ENABLED", "true")
    from synth_engine.shared.settings import get_settings

    get_settings.cache_clear()

    app = FastAPI()
    from synth_engine.bootstrapper.dependencies.permissions import require_permission

    @app.post("/test/admin")
    def _admin_op(ctx: Any = Depends(require_permission("admin:users"))) -> dict[str, str]:  # noqa: B008
        return {"role": ctx.role, "org_id": ctx.org_id}

    client = TestClient(app, raise_server_exceptions=False)
    # No Authorization header — pass-through mode
    response = client.post("/test/admin")

    assert response.status_code == 200
    body = response.json()
    assert body["role"] == "admin"


# ---------------------------------------------------------------------------
# T80.3: Admin user management — schemas
# ---------------------------------------------------------------------------


def test_user_create_request_validates_role_field() -> None:
    """UserCreateRequest rejects invalid role values.

    Schema validation must enforce the Role enum for role field.
    Invalid roles must raise a validation error.
    """
    from pydantic import ValidationError

    from synth_engine.bootstrapper.schemas.admin_users import UserCreateRequest

    with pytest.raises(ValidationError) as exc_info:
        UserCreateRequest(email="test@example.com", role="superadmin")

    errors = exc_info.value.errors()
    assert len(errors) >= 1
    # The error must reference the role field
    assert any(e["loc"] == ("role",) for e in errors)


def test_user_create_request_accepts_valid_roles() -> None:
    """UserCreateRequest accepts all four valid role values.

    Schema validation must accept: admin, operator, viewer, auditor.
    """
    from synth_engine.bootstrapper.schemas.admin_users import UserCreateRequest

    for role in ("admin", "operator", "viewer", "auditor"):
        req = UserCreateRequest(email="test@example.com", role=role)
        assert req.role == role


def test_user_patch_request_accepts_none_role() -> None:
    """UserPatchRequest accepts None for role (partial update).

    Admin may patch other user fields without specifying a new role.
    """
    from synth_engine.bootstrapper.schemas.admin_users import UserPatchRequest

    req = UserPatchRequest(role=None)
    assert req.role is None


def test_user_patch_request_rejects_invalid_role() -> None:
    """UserPatchRequest rejects invalid role values.

    The escalation guard at the schema level: 'superadmin' must be rejected.
    """
    from pydantic import ValidationError

    from synth_engine.bootstrapper.schemas.admin_users import UserPatchRequest

    with pytest.raises(ValidationError) as exc_info:
        UserPatchRequest(role="superadmin")

    errors = exc_info.value.errors()
    assert any(e["loc"] == ("role",) for e in errors)


def test_user_response_schema_contains_expected_fields() -> None:
    """UserResponse schema exposes id, org_id, email, role, created_at.

    Verifies the response schema structure for admin user management.
    """
    from synth_engine.bootstrapper.schemas.admin_users import UserResponse

    fields = set(UserResponse.model_fields.keys())
    assert "id" in fields
    assert "org_id" in fields
    assert "email" in fields
    assert "role" in fields


# ---------------------------------------------------------------------------
# T80.3: _check_last_admin_guard helper
# ---------------------------------------------------------------------------


def test_last_admin_guard_returns_409_when_single_admin() -> None:
    """_check_last_admin_guard returns 409 JSONResponse when count==1.

    When there is exactly one admin in the org, deactivation or demotion of
    that admin must be blocked with 409 Conflict.
    """
    from synth_engine.bootstrapper.routers.admin_users import _check_last_admin_guard

    result = _check_last_admin_guard(admin_count=1, target_user_id="some-user-id")
    assert result is not None
    assert result.status_code == 409


def test_last_admin_guard_returns_none_when_multiple_admins() -> None:
    """_check_last_admin_guard returns None when count > 1.

    When multiple admins exist, removing one admin is safe.
    Returns None (check passes — operation is permitted).
    """
    from synth_engine.bootstrapper.routers.admin_users import _check_last_admin_guard

    result = _check_last_admin_guard(admin_count=2, target_user_id="some-user-id")
    assert result is None


def test_last_admin_guard_returns_none_when_count_zero() -> None:
    """_check_last_admin_guard returns None when count==0 (edge case).

    Edge case: if admin count is already 0 (data inconsistency), don't block.
    count=0 means no admins currently exist — the target cannot be "the last admin".
    """
    from synth_engine.bootstrapper.routers.admin_users import _check_last_admin_guard

    result = _check_last_admin_guard(admin_count=0, target_user_id="some-user-id")
    assert result is None


def test_last_admin_guard_409_body_has_specific_detail() -> None:
    """_check_last_admin_guard 409 body contains specific detail text.

    The response must clearly explain why the operation was rejected.
    Specific value assertion for the detail field.
    """
    from synth_engine.bootstrapper.routers.admin_users import _check_last_admin_guard

    result = _check_last_admin_guard(admin_count=1, target_user_id="test-user")
    assert result is not None

    import json

    body = json.loads(result.body)
    assert body["status"] == 409
    assert "last admin" in body["detail"].lower() or "admin" in body["detail"].lower()


# ---------------------------------------------------------------------------
# T80.4: Audit log endpoint — _emit_audit_log_access_event
# ---------------------------------------------------------------------------


def test_emit_audit_log_access_event_calls_log_event() -> None:
    """_emit_audit_log_access_event calls get_audit_logger().log_event().

    Verifies the 'audit the auditor' requirement: every audit log access
    must emit an audit event.
    """
    from synth_engine.bootstrapper.routers.compliance import _emit_audit_log_access_event

    mock_audit = MagicMock()
    with patch(
        "synth_engine.bootstrapper.routers.compliance.get_audit_logger",
        return_value=mock_audit,
    ):
        _emit_audit_log_access_event(actor="test-actor", org_id=_ORG_A_UUID)

    mock_audit.log_event.assert_called_once()
    kwargs = mock_audit.log_event.call_args[1]
    assert kwargs["event_type"] == "AUDIT_LOG_ACCESS"
    assert kwargs["actor"] == "test-actor"
    assert kwargs["resource"] == "audit_log"
    assert kwargs["action"] == "read"


def test_emit_audit_log_access_event_swallows_audit_failure() -> None:
    """_emit_audit_log_access_event does not raise on audit write failure.

    Audit log access must not be blocked by audit write failures — the
    read operation should proceed even if the audit event fails to write.
    Specific value assertion: returns None (not an exception or error response).
    """
    from synth_engine.bootstrapper.routers.compliance import _emit_audit_log_access_event

    mock_audit = MagicMock()
    mock_audit.log_event.side_effect = OSError("Disk full")

    with patch(
        "synth_engine.bootstrapper.routers.compliance.get_audit_logger",
        return_value=mock_audit,
    ):
        result = _emit_audit_log_access_event(actor="test-actor", org_id=_ORG_A_UUID)

    assert result is None


# ---------------------------------------------------------------------------
# T80.5: Erasure semantics — _check_erasure_admin_idor
# ---------------------------------------------------------------------------


def test_check_erasure_admin_idor_returns_none_same_org() -> None:
    """_check_erasure_admin_idor returns None when subject is in admin's org.

    Admin can erase any subject within their org.
    Returns None (check passes).
    """
    from synth_engine.bootstrapper.routers.compliance import _check_erasure_admin_idor

    result = _check_erasure_admin_idor(
        subject_org_id=_ORG_A_UUID,
        admin_org_id=_ORG_A_UUID,
        actor=_USER_ADMIN_UUID,
    )
    assert result is None


def test_check_erasure_admin_idor_returns_404_different_org() -> None:
    """_check_erasure_admin_idor returns 404 when subject is in different org.

    Cross-org erasure must be blocked with 404 (IDOR protection).
    Returns 404 JSONResponse.
    """
    from synth_engine.bootstrapper.routers.compliance import _check_erasure_admin_idor

    result = _check_erasure_admin_idor(
        subject_org_id="22222222-2222-2222-2222-222222222222",
        admin_org_id=_ORG_A_UUID,
        actor=_USER_ADMIN_UUID,
    )
    assert result is not None
    assert result.status_code == 404


def test_check_erasure_admin_idor_emits_audit_on_cross_org() -> None:
    """_check_erasure_admin_idor emits audit event on cross-org attempt.

    Cross-org erasure attempts must be logged for intrusion detection.
    """
    from synth_engine.bootstrapper.routers.compliance import _check_erasure_admin_idor

    mock_audit = MagicMock()
    with patch(
        "synth_engine.bootstrapper.routers.compliance.get_audit_logger",
        return_value=mock_audit,
    ):
        _check_erasure_admin_idor(
            subject_org_id="22222222-2222-2222-2222-222222222222",
            admin_org_id=_ORG_A_UUID,
            actor=_USER_ADMIN_UUID,
        )

    mock_audit.log_event.assert_called_once()
    kwargs = mock_audit.log_event.call_args[1]
    assert kwargs["event_type"] == "COMPLIANCE_ERASURE_IDOR_ATTEMPT"
    assert kwargs["actor"] == _USER_ADMIN_UUID


# ---------------------------------------------------------------------------
# T80.0: ADR-0066 existence check
# ---------------------------------------------------------------------------


def test_adr_0066_file_exists() -> None:
    """ADR-0066-rbac-permission-model.md exists in docs/adr/.

    The RBAC architectural decision record must be created as part of this phase.
    """
    import os

    adr_path = os.path.join(
        os.path.dirname(__file__),
        "..",
        "..",
        "docs",
        "adr",
        "ADR-0066-rbac-permission-model.md",
    )
    assert os.path.exists(adr_path), (
        f"ADR-0066 not found at {adr_path}. "
        "The RBAC ADR must be created as part of T80.0."
    )


def test_adr_0049_is_superseded() -> None:
    """ADR-0049 status must be 'Superseded' with reference to ADR-0066.

    ADR-0049 (scope-based authorization) is superseded by ADR-0066 (RBAC).
    """
    import os

    adr_0049_path = os.path.join(
        os.path.dirname(__file__),
        "..",
        "..",
        "docs",
        "adr",
        "ADR-0049-scope-based-authorization.md",
    )
    with open(adr_0049_path) as f:
        content = f.read()

    assert "Superseded" in content, (
        "ADR-0049 must have its status updated to 'Superseded' by ADR-0066."
    )
    assert "ADR-0066" in content, (
        "ADR-0049 must reference ADR-0066 (the superseding document)."
    )


# ---------------------------------------------------------------------------
# Token issuance — DB-resolved role
# ---------------------------------------------------------------------------


def test_create_token_embeds_role_in_jwt() -> None:
    """create_token embeds the specified role in the JWT payload.

    Token issuance must include role claim — client cannot specify it.
    Specific value assertion: decoded role matches input.
    """
    from synth_engine.bootstrapper.dependencies.auth import create_token, verify_token

    # Monkeypatch is not available here — use a real settings-backed test
    # We test the data contract: role appears in the decoded token
    # Note: this test just validates the create_token contract, not the
    # full issuance flow (that requires settings with a real secret)
    import os

    secret = "test-secret-long-enough-32chars+"  # pragma: allowlist secret
    os.environ["JWT_SECRET_KEY"] = secret  # noqa: B003
    os.environ["JWT_ALGORITHM"] = "HS256"

    try:
        from synth_engine.shared.settings import get_settings

        get_settings.cache_clear()

        token = create_token(sub="test-user", scope=["read"], org_id=_ORG_A_UUID, role="viewer")
        claims = verify_token(token)
        assert claims["role"] == "viewer"
        assert claims["sub"] == "test-user"
        assert claims["org_id"] == _ORG_A_UUID
    finally:
        os.environ.pop("JWT_SECRET_KEY", None)
        os.environ.pop("JWT_ALGORITHM", None)
        from synth_engine.shared.settings import get_settings

        get_settings.cache_clear()


def test_admin_users_router_exists() -> None:
    """admin_users router module is importable with a router attribute.

    Rule 8 compliance: the router must exist and be wirable.
    """
    from synth_engine.bootstrapper.routers.admin_users import router

    assert router is not None
    assert hasattr(router, "routes")
    assert len(router.routes) > 0


def test_admin_users_router_wired_in_registry() -> None:
    """admin_users router is registered in router_registry.py.

    Rule 8 (IoC wiring): the new router must be wired into the application.
    """
    import inspect

    from synth_engine.bootstrapper import router_registry

    source = inspect.getsource(router_registry)
    assert "admin_users" in source, (
        "admin_users router must be imported and included in router_registry.py"
    )
