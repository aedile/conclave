"""FastAPI router for admin user management — Phase 80.

Implements:
- POST /admin/users — create user in org (admin only)
- GET /admin/users — list users in org (admin only, paginated)
- PATCH /admin/users/{user_id} — update role (admin only)
- DELETE /admin/users/{user_id} — deactivate user (admin only)

Security posture
----------------
- All endpoints require ``admin:users`` permission via ``require_permission()``.
  Calls ``get_current_user()`` internally → 401 if no/bad JWT, 403 if not admin.
- All operations are scoped to the authenticated admin's org_id.
  Cross-org user management returns 404 (IDOR protection, ADR-0066 section 4).
- Last-admin guard: deactivation and demotion check
  ``SELECT COUNT(*) FROM users WHERE org_id = :org_id AND role = 'admin' FOR UPDATE``.
  If count == 1 and target is admin, return 409 Conflict.
  The ``FOR UPDATE`` row lock prevents TOCTOU races where two concurrent requests
  each see count=2 and both proceed — leaving zero admins (B2 fix).
- Audit events emitted BEFORE mutations (T68.3 audit-before-commit).
- Role escalation is prevented at schema level: ``UserPatchRequest.role`` is a
  ``Literal["admin","operator","viewer","auditor"]`` — unknown roles are rejected
  with 422 before any business logic runs.

RFC 7807 Problem Details format for all error responses.

Boundary constraints (import-linter enforced):
    - ``bootstrapper/`` may import from ``shared/`` and ``modules/``.
    - This router does NOT import from any ``modules/`` package directly.

CONSTITUTION Priority 0: Security — IDOR, last-admin guard, audit trail
CONSTITUTION Priority 5: Code Quality — strict typing, Google docstrings
Phase: 80 — Role-Based Access Control (T80.3)
ADR: ADR-0066 — RBAC Permission Model
"""

from __future__ import annotations

import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session, col, select

from synth_engine.bootstrapper.dependencies.db import get_db_session
from synth_engine.bootstrapper.dependencies.permissions import require_permission
from synth_engine.bootstrapper.dependencies.tenant import TenantContext
from synth_engine.bootstrapper.errors import problem_detail
from synth_engine.bootstrapper.openapi_metadata import COMMON_ERROR_RESPONSES
from synth_engine.bootstrapper.schemas.admin_users import (
    UserCreateRequest,
    UserListResponse,
    UserPatchRequest,
    UserResponse,
)
from synth_engine.shared.models.user import User
from synth_engine.shared.observability import AUDIT_WRITE_FAILURE_TOTAL
from synth_engine.shared.security.audit import get_audit_logger

_logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin-users"])

#: Type alias for the validated user_id path parameter.
_UserIdPath = Annotated[str, Path(min_length=1, max_length=36)]

#: Default page size for GET /admin/users.
_DEFAULT_USER_PAGE_SIZE: int = 100

#: Maximum page size a caller may request for GET /admin/users.
_MAX_USER_PAGE_SIZE: int = 200


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _check_last_admin_guard(*, admin_count: int, target_user_id: str) -> JSONResponse | None:
    """Return 409 if removing/demoting the target would leave the org with no admins.

    Applies to both deactivation (DELETE) and demotion (PATCH to non-admin role).
    If ``admin_count == 1``, the target IS the last admin — operation is rejected.
    If ``admin_count == 0``, data inconsistency — no admin to protect, pass through.
    If ``admin_count >= 2``, operation is safe.

    Args:
        admin_count: Current number of admin users in the org.
        target_user_id: UUID of the user being deactivated or demoted.

    Returns:
        A 409 JSONResponse if the guard triggers; None if the operation is safe.
    """
    if admin_count == 1:
        _logger.warning(
            "Last-admin guard triggered for user=%s (admin_count=%d)",
            target_user_id,
            admin_count,
        )
        return JSONResponse(
            status_code=409,
            content=problem_detail(
                status=409,
                title="Conflict",
                detail=(
                    "Cannot remove or demote the last admin in the organization. "
                    "Promote another user to admin first."
                ),
            ),
            media_type="application/problem+json",
        )
    return None


def _get_org_admin_count(session: Session, org_id: str) -> int:
    """Count admin users in an org using a pessimistic row lock (FOR UPDATE).

    The ``SELECT ... FOR UPDATE`` lock prevents a TOCTOU race where two concurrent
    demotion/deactivation requests each see count=2 and both proceed — leaving the
    org with zero admins.  The lock is released when the outer transaction commits
    or rolls back (per standard PostgreSQL behavior).

    Args:
        session: Open SQLModel Session.
        org_id: UUID string of the organization to query.

    Returns:
        Integer count of users with role='admin' in the org.
    """
    try:
        org_uuid = uuid.UUID(org_id)
    except ValueError:
        return 0
    stmt = (
        select(col(User.id))
        .where(
            User.org_id == org_uuid,
            User.role == "admin",
        )
        .with_for_update()
    )
    results = session.exec(stmt).all()
    return len(results)


def _find_user_in_org(session: Session, user_id: str, org_id: str) -> User | None:
    """Fetch a user by user_id, scoped to the admin's org (IDOR protection).

    Args:
        session: Open SQLModel Session.
        user_id: UUID string of the target user.
        org_id: UUID string of the admin's organization.

    Returns:
        The :class:`~synth_engine.shared.models.user.User` ORM row if found
        in the org, else None.
    """
    try:
        user_uuid = uuid.UUID(user_id)
        org_uuid = uuid.UUID(org_id)
    except ValueError:
        return None

    stmt = select(User).where(
        col(User.id) == user_uuid,
        User.org_id == org_uuid,
    )
    return session.exec(stmt).first()


def _not_found_response(user_id: str) -> JSONResponse:
    """Return a 404 JSONResponse for a user not found in the org.

    Args:
        user_id: The requested user UUID (included in error detail for
            client usability — not a security leak because 404 is returned
            regardless of whether the user exists in another org).

    Returns:
        A 404 JSONResponse with RFC 7807 Problem Details body.
    """
    return JSONResponse(
        status_code=404,
        content=problem_detail(
            status=404,
            title="Not Found",
            detail=f"User {user_id} not found in your organization.",
        ),
        media_type="application/problem+json",
    )


def _emit_user_event(
    *,
    event_type: str,
    actor: str,
    target_user_id: str,
    org_id: str,
    details: dict[str, str],
) -> JSONResponse | None:
    """Emit a WORM audit event for user management actions (T68.3).

    Returns None on success; a 500 JSONResponse if the audit write fails.
    Callers should abort the operation if this returns non-None.

    Args:
        event_type: Audit event type string (e.g. ``"USER_CREATED"``).
        actor: Authenticated admin's user_id.
        target_user_id: The affected user's UUID string.
        org_id: Organization UUID string.
        details: Additional event details dict.

    Returns:
        None on success; a 500 JSONResponse on audit write failure.
    """
    try:
        get_audit_logger().log_event(
            event_type=event_type,
            actor=actor,
            resource=f"user/{target_user_id}",
            action=event_type.lower(),
            details={"org_id": org_id, **details},
        )
        return None
    except (ValueError, OSError, UnicodeError):
        AUDIT_WRITE_FAILURE_TOTAL.labels(
            router="admin-users", endpoint=f"/{event_type.lower()}"
        ).inc()
        _logger.exception(
            "Audit logging failed for %s (user=%s, org=%s); aborting (T68.3)",
            event_type,
            target_user_id,
            org_id,
        )
        return JSONResponse(
            status_code=500,
            content=problem_detail(
                status=500,
                title="Internal Server Error",
                detail="Audit write failed. Operation was not performed.",
            ),
        )


# ---------------------------------------------------------------------------
# POST /admin/users — create user in org
# ---------------------------------------------------------------------------


@router.post(
    "/users",
    summary="Create a user in the org",
    description=(
        "Create a new user in the authenticated admin's organization. "
        "Admin role required. Role must be one of: admin, operator, viewer, auditor."
    ),
    responses=COMMON_ERROR_RESPONSES,
    response_model=UserResponse,
    status_code=201,
)
def create_user(
    body: UserCreateRequest,
    session: Annotated[Session, Depends(get_db_session)],
    ctx: Annotated[TenantContext, Depends(require_permission("admin:users"))],
) -> UserResponse | JSONResponse:
    """Create a new user in the authenticated admin's organization.

    Emits a ``USER_CREATED`` audit event BEFORE the database write (T68.3).

    Args:
        body: JSON body with ``email`` and ``role``.
        session: Database session (injected by FastAPI DI).
        ctx: Resolved tenant context from ``require_permission("admin:users")``.

    Returns:
        :class:`UserResponse` on success (HTTP 201), or RFC 7807 error response.
    """
    try:
        org_uuid = uuid.UUID(ctx.org_id)
    except ValueError:
        return JSONResponse(
            status_code=400,
            content=problem_detail(
                status=400,
                title="Bad Request",
                detail="Invalid org_id in token.",
            ),
        )

    new_user = User(org_id=org_uuid, email=body.email, role=body.role)

    audit_err = _emit_user_event(
        event_type="USER_CREATED",
        actor=ctx.user_id,
        target_user_id=str(new_user.id),
        org_id=ctx.org_id,
        details={"email_hash": str(len(body.email)), "role": body.role},
    )
    if audit_err is not None:
        return audit_err

    try:
        session.add(new_user)
        session.commit()
        session.refresh(new_user)
    except SQLAlchemyError:
        session.rollback()
        _logger.warning(
            "create_user: DB error for org=%s",
            ctx.org_id,
            exc_info=True,
        )
        return JSONResponse(
            status_code=500,
            content=problem_detail(
                status=500,
                title="Internal Server Error",
                detail="Database operation failed. Please retry.",
            ),
        )

    _logger.info(
        "User created: user_id=%s org=%s role=%s (by admin=%s)",
        new_user.id,
        ctx.org_id,
        body.role,
        ctx.user_id,
    )
    return UserResponse.model_validate(new_user)


# ---------------------------------------------------------------------------
# GET /admin/users — list users in org
# ---------------------------------------------------------------------------


@router.get(
    "/users",
    summary="List users in the org",
    description=(
        "Return users in the authenticated admin's organization. "
        "Supports pagination via limit and offset query parameters."
    ),
    responses=COMMON_ERROR_RESPONSES,
    response_model=UserListResponse,
)
def list_users(
    session: Annotated[Session, Depends(get_db_session)],
    ctx: Annotated[TenantContext, Depends(require_permission("admin:users"))],
    limit: int = Query(
        default=_DEFAULT_USER_PAGE_SIZE,
        ge=1,
        le=_MAX_USER_PAGE_SIZE,
        description="Maximum number of users to return (1-200, default 100).",
    ),
    offset: int = Query(
        default=0,
        ge=0,
        description="Number of users to skip for pagination.",
    ),
) -> UserListResponse:
    """List users in the authenticated admin's organization with pagination.

    Args:
        session: Database session (injected by FastAPI DI).
        ctx: Resolved tenant context from ``require_permission("admin:users")``.
        limit: Maximum number of users to return (1-200, default 100).
        offset: Number of users to skip (for pagination).

    Returns:
        :class:`UserListResponse` with paginated users in the org.
    """
    try:
        org_uuid = uuid.UUID(ctx.org_id)
    except ValueError:
        return UserListResponse(items=[], total=0)

    stmt = select(User).where(User.org_id == org_uuid).offset(offset).limit(limit)
    users = session.exec(stmt).all()
    return UserListResponse(
        items=[UserResponse.model_validate(u) for u in users],
        total=len(users),
    )


# ---------------------------------------------------------------------------
# PATCH /admin/users/{user_id} — update role
# ---------------------------------------------------------------------------


@router.patch(
    "/users/{user_id}",
    summary="Update a user's role",
    description=(
        "Update the role of a user in the authenticated admin's organization. "
        "Cannot demote the last admin (409 Conflict). "
        "Cannot assign roles outside: admin, operator, viewer, auditor."
    ),
    responses=COMMON_ERROR_RESPONSES,
    response_model=UserResponse,
)
def patch_user(
    user_id: _UserIdPath,
    body: UserPatchRequest,
    session: Annotated[Session, Depends(get_db_session)],
    ctx: Annotated[TenantContext, Depends(require_permission("admin:users"))],
) -> UserResponse | JSONResponse:
    """Update a user's role within the authenticated admin's organization.

    Applies last-admin guard when the new role is not ``admin`` and the
    target user is currently admin.  Uses ``SELECT ... FOR UPDATE`` to
    prevent TOCTOU races in concurrent demotion requests.

    T68.3: Emits ``RBAC_ROLE_CHANGED`` audit event BEFORE the DB update.

    Args:
        user_id: UUID of the target user (path parameter).
        body: JSON body with optional ``role`` field.
        session: Database session (injected by FastAPI DI).
        ctx: Resolved tenant context from ``require_permission("admin:users")``.

    Returns:
        :class:`UserResponse` on success; RFC 7807 404/409/500 on error.
    """
    target_user = _find_user_in_org(session, user_id, ctx.org_id)
    if target_user is None:
        return _not_found_response(user_id)

    # Apply last-admin guard if this PATCH demotes an admin to a lower role.
    if body.role is not None and body.role != "admin" and target_user.role == "admin":
        admin_count = _get_org_admin_count(session, ctx.org_id)
        guard_err = _check_last_admin_guard(
            admin_count=admin_count,
            target_user_id=user_id,
        )
        if guard_err is not None:
            return guard_err

    if body.role is None:
        # No-op: return current state
        return UserResponse.model_validate(target_user)

    old_role = target_user.role

    audit_err = _emit_user_event(
        event_type="RBAC_ROLE_CHANGED",
        actor=ctx.user_id,
        target_user_id=user_id,
        org_id=ctx.org_id,
        details={"old_role": old_role, "new_role": body.role},
    )
    if audit_err is not None:
        return audit_err

    target_user.role = body.role
    session.add(target_user)
    try:
        session.commit()
        session.refresh(target_user)
    except SQLAlchemyError:
        session.rollback()
        _logger.warning(
            "patch_user: DB error for user=%s org=%s",
            user_id,
            ctx.org_id,
            exc_info=True,
        )
        return JSONResponse(
            status_code=500,
            content=problem_detail(
                status=500,
                title="Internal Server Error",
                detail="Database operation failed. Please retry.",
            ),
        )

    _logger.info(
        "User role changed: user_id=%s org=%s old=%s new=%s (by admin=%s)",
        user_id,
        ctx.org_id,
        old_role,
        body.role,
        ctx.user_id,
    )
    return UserResponse.model_validate(target_user)


# ---------------------------------------------------------------------------
# DELETE /admin/users/{user_id} — deactivate user
# ---------------------------------------------------------------------------


@router.delete(
    "/users/{user_id}",
    summary="Deactivate a user",
    description=(
        "Deactivate (remove) a user from the authenticated admin's organization. "
        "Cannot deactivate the last admin (409 Conflict)."
    ),
    responses=COMMON_ERROR_RESPONSES,
    status_code=204,
)
def delete_user(
    user_id: _UserIdPath,
    session: Annotated[Session, Depends(get_db_session)],
    ctx: Annotated[TenantContext, Depends(require_permission("admin:users"))],
) -> JSONResponse:
    """Deactivate a user in the authenticated admin's organization.

    Applies last-admin guard: if the target user is the last admin, returns
    409 Conflict.  Uses ``SELECT ... FOR UPDATE`` to prevent TOCTOU races.
    T68.3: emits ``USER_DEACTIVATED`` audit event BEFORE deletion.

    Args:
        user_id: UUID of the target user (path parameter).
        session: Database session (injected by FastAPI DI).
        ctx: Resolved tenant context from ``require_permission("admin:users")``.

    Returns:
        HTTP 204 on success; RFC 7807 404/409/500 on error.
    """
    target_user = _find_user_in_org(session, user_id, ctx.org_id)
    if target_user is None:
        return _not_found_response(user_id)

    # Apply last-admin guard for deactivation of an admin user.
    if target_user.role == "admin":
        admin_count = _get_org_admin_count(session, ctx.org_id)
        guard_err = _check_last_admin_guard(
            admin_count=admin_count,
            target_user_id=user_id,
        )
        if guard_err is not None:
            return guard_err

    audit_err = _emit_user_event(
        event_type="USER_DEACTIVATED",
        actor=ctx.user_id,
        target_user_id=user_id,
        org_id=ctx.org_id,
        details={"role": target_user.role},
    )
    if audit_err is not None:
        return audit_err

    try:
        session.delete(target_user)
        session.commit()
    except SQLAlchemyError:
        session.rollback()
        _logger.warning(
            "delete_user: DB error for user=%s org=%s",
            user_id,
            ctx.org_id,
            exc_info=True,
        )
        return JSONResponse(
            status_code=500,
            content=problem_detail(
                status=500,
                title="Internal Server Error",
                detail="Database operation failed. Please retry.",
            ),
        )

    _logger.info(
        "User deactivated: user_id=%s org=%s (by admin=%s)",
        user_id,
        ctx.org_id,
        ctx.user_id,
    )
    return JSONResponse(status_code=204, content=None)
