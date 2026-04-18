"""Role-Based Access Control (RBAC) permission enforcement — Phase 80.

Provides:
- :class:`Role`: Enum of the four RBAC roles (admin, operator, viewer, auditor).
- :data:`PERMISSION_MATRIX`: Static frozen mapping of permission strings to the
  frozenset of :class:`Role` values that are allowed to perform that operation.
  Wrapped in :class:`types.MappingProxyType` to prevent runtime mutation.
- :func:`has_permission`: Pure function for testing whether a role has a permission.
- :func:`require_permission`: FastAPI dependency factory that enforces a permission
  requirement and returns the :class:`~synth_engine.bootstrapper.dependencies.tenant.TenantContext`.
- :data:`RBAC_403_DETAIL`: The static error message returned on permission denial.

Permission Enforcement Model (ADR-0066)
----------------------------------------
``require_permission(permission)`` wraps ``get_current_user()`` and adds a
permission check:

1. ``get_current_user()`` → :class:`TenantContext(org_id, user_id, role)` (401 if invalid JWT)
2. Look up ``role`` in :data:`PERMISSION_MATRIX`
3. If role not allowed → raise ``HTTPException(403, RBAC_403_DETAIL)``
4. If role allowed → return ``TenantContext``

Error ordering: 401 (no/bad JWT) → 403 (wrong role) → 404 (wrong org, at query time).
This is correct: role is not a secret (the user knows their own role), but
org existence of OTHER orgs must never be disclosed (IDOR prevention).

Pass-Through Mode
-----------------
When ``jwt_secret_key`` is empty AND ``conclave_pass_through_enabled=True``
AND NOT in production mode, ``get_current_user`` returns a sentinel with
``role="admin"``. ``require_permission`` then passes for ALL permissions
because admin holds all of them.

Module Boundary
---------------
This module belongs in ``bootstrapper/dependencies/`` — NOT in ``shared/``.
Domain modules (``modules/``) have no need for HTTP-level access control.
The permission matrix is an authorization policy, not a shared data contract.

CONSTITUTION Priority 0: Security — access control enforcement
CONSTITUTION Priority 5: Code Quality — strict typing, Google docstrings
Phase: 80 — Role-Based Access Control (T80.1, T80.2)
ADR: ADR-0066 — RBAC Permission Model
"""

from __future__ import annotations

import enum
import logging
import types
from collections.abc import Callable

from fastapi import Depends, HTTPException
from starlette.requests import Request

from synth_engine.bootstrapper.dependencies.tenant import TenantContext, get_current_user

_logger = logging.getLogger(__name__)

#: Static error message returned on permission denial (403).
#: Must be a specific value — not an internal detail.
RBAC_403_DETAIL: str = "Insufficient permissions"


class Role(str, enum.Enum):
    """RBAC role values for the Conclave Engine.

    Each authenticated user has exactly one role per organization (per the
    org-scoped JWT in ADR-0065).  Roles are embedded in the JWT and cannot
    be changed without re-issuing the token.

    Attributes:
        admin: Full org control, user management, all operations.
        operator: Synthesis job lifecycle, connection management.
        viewer: Read-only access to jobs and connections.
        auditor: Compliance-only access to audit log and privacy ledger.
    """

    admin = "admin"
    operator = "operator"
    viewer = "viewer"
    auditor = "auditor"


#: Static permission matrix mapping permission strings to the frozenset of
#: :class:`Role` values that are authorized for each operation.
#:
#: This is the canonical authorization policy for the Conclave Engine at Tier 8.
#: Changes to this matrix must be documented in ADR-0066.
#:
#: Security: wrapped in :class:`types.MappingProxyType` to prevent runtime
#: mutation.  Values are frozensets (immutable inner collections).
PERMISSION_MATRIX: types.MappingProxyType[str, frozenset[Role]] = types.MappingProxyType(
    {
        # Connection management
        "connections:create": frozenset({Role.admin, Role.operator}),
        "connections:read": frozenset({Role.admin, Role.operator, Role.viewer}),
        "connections:delete": frozenset({Role.admin, Role.operator}),
        # Synthesis job lifecycle
        "jobs:create": frozenset({Role.admin, Role.operator}),
        "jobs:read": frozenset({Role.admin, Role.operator, Role.viewer}),
        # jobs:cancel is reserved for future endpoint implementation — currently documented
        # in ADR-0066 as a planned permission. No cancel endpoint exists yet.
        "jobs:cancel": frozenset({Role.admin, Role.operator}),
        "jobs:download": frozenset({Role.admin, Role.operator, Role.viewer}),
        "jobs:shred": frozenset({Role.admin, Role.operator}),
        "jobs:legal-hold": frozenset({Role.admin}),
        # Webhook management
        "webhooks:write": frozenset({Role.admin, Role.operator}),
        "webhooks:read": frozenset({Role.admin, Role.operator, Role.viewer}),
        # Privacy budget
        "privacy:read": frozenset({Role.admin, Role.operator, Role.viewer, Role.auditor}),
        "privacy:reset": frozenset({Role.admin}),
        # Compliance
        "compliance:erasure": frozenset({Role.admin}),
        "compliance:audit-read": frozenset({Role.admin, Role.auditor}),
        # Security operations
        "security:admin": frozenset({Role.admin}),
        # Administrative
        "admin:users": frozenset({Role.admin}),
        "admin:settings": frozenset({Role.admin}),
        # Settings
        "settings:read": frozenset({Role.admin, Role.operator, Role.viewer}),
        "settings:write": frozenset({Role.admin}),
        # Session management (Phase 81 — ADR-0067)
        "sessions:revoke": frozenset({Role.admin}),
    }
)


def has_permission(*, role: str, permission: str) -> bool:
    """Return True if the given role has the specified permission.

    Pure function for testing and programmatic permission checks.
    Fail-closed: returns False for any unknown role or permission string.

    Args:
        role: The role string to check (e.g. ``"admin"``).
        permission: The permission string to check (e.g. ``"jobs:create"``).

    Returns:
        ``True`` if the role is in the allowed set for the permission;
        ``False`` if the permission is unknown or the role is not allowed.

    Examples::

        has_permission(role="admin", permission="jobs:create")   # True
        has_permission(role="viewer", permission="jobs:create")  # False
        has_permission(role="auditor", permission="compliance:audit-read")  # True
    """
    try:
        role_enum = Role(role)
    except ValueError:
        return False

    allowed_roles = PERMISSION_MATRIX.get(permission)
    if allowed_roles is None:
        return False

    return role_enum in allowed_roles


def require_permission(permission: str) -> Callable[..., TenantContext]:
    """Return a FastAPI dependency that enforces the given permission.

    Resolves ``get_current_user`` to obtain ``TenantContext(org_id, user_id, role)``,
    then checks the role against the static :data:`PERMISSION_MATRIX`.

    Error ordering (per ADR-0066):
    - 401: ``get_current_user`` raises if JWT is absent, invalid, or expired
    - 403: role not in allowed set for this permission
    - 404: resource not found in user's org (enforced at DB query level, not here)

    **Pass-through mode**: when ``jwt_secret_key`` is empty AND
    ``conclave_pass_through_enabled=True`` AND NOT in production, the sentinel
    ``TenantContext(role="admin")`` passes for all permissions.

    Args:
        permission: The permission string required for this endpoint, e.g.
            ``"jobs:create"``. Must be a key in :data:`PERMISSION_MATRIX`.

    Returns:
        A FastAPI-compatible dependency callable that returns :class:`TenantContext`
        on success or raises :exc:`~fastapi.HTTPException` on failure.
        The returned callable raises HTTPException 401/403 when authentication
        or authorization fails (see error ordering above).

    Example::

        @router.get("/jobs")
        async def list_jobs(
            ctx: Annotated[TenantContext, Depends(require_permission("jobs:read"))],
        ) -> JobListResponse: ...
    """

    def _check_permission(
        request: Request,
        ctx: TenantContext = Depends(get_current_user),  # noqa: B008
    ) -> TenantContext:
        """Enforce the permission requirement for this endpoint.

        Args:
            request: The incoming HTTP request (auto-injected by FastAPI).
            ctx: Resolved :class:`TenantContext` from ``get_current_user``
                (auto-injected by FastAPI DI).

        Returns:
            The :class:`TenantContext` on success.

        Raises:
            HTTPException: 403 if the role does not have the required permission.
        """
        if not has_permission(role=ctx.role, permission=permission):
            _logger.debug(
                "Permission denied: user=%s org=%s role=%r lacks permission=%r",
                ctx.user_id,
                ctx.org_id,
                ctx.role,
                permission,
            )
            raise HTTPException(
                status_code=403,
                detail=RBAC_403_DETAIL,
            )
        return ctx

    return _check_permission
