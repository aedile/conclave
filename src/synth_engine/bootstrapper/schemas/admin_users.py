"""Request/response schemas for admin user management — Phase 80.

Provides Pydantic schemas for the admin user management endpoints:
- :class:`UserCreateRequest`: create a new user in the org
- :class:`UserPatchRequest`: update a user's role (partial update)
- :class:`UserResponse`: single user representation
- :class:`UserListResponse`: paginated list of users

Role validation is enforced at schema level for all inputs that accept
a role field.  Invalid role values (e.g. ``"superadmin"``) are rejected
with HTTP 422 before any business logic runs.

CONSTITUTION Priority 0: Security — input validation at schema boundary
CONSTITUTION Priority 5: Code Quality — strict typing, Google docstrings
Phase: 80 — Role-Based Access Control (T80.3)
"""

from __future__ import annotations

import datetime
import uuid
from typing import Literal

from pydantic import BaseModel, Field

#: Type alias for the four valid role strings.
#: Used as a discriminated union to prevent invalid role injection.
RoleLiteral = Literal["admin", "operator", "viewer", "auditor"]


class UserCreateRequest(BaseModel):
    """Request body for POST /admin/users.

    Attributes:
        email: User email address. Used as the display name and login identifier.
        role: RBAC role to assign.  Must be one of: admin, operator, viewer, auditor.
            Defaults to ``operator`` for backward compatibility.
    """

    email: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="User email address.",
    )
    role: RoleLiteral = Field(
        default="operator",
        description="RBAC role: admin, operator, viewer, or auditor.",
    )


class UserPatchRequest(BaseModel):
    """Request body for PATCH /admin/users/{user_id}.

    Partial update: all fields are optional.  Only supplied fields are updated.

    Attributes:
        role: New RBAC role.  If absent, the existing role is unchanged.
            Must be one of: admin, operator, viewer, auditor.
            Cannot be an arbitrary string (privilege escalation guard at schema level).
    """

    role: RoleLiteral | None = Field(
        default=None,
        description="New RBAC role: admin, operator, viewer, or auditor. "
        "If absent, the existing role is not changed.",
    )


class UserResponse(BaseModel):
    """Response schema for a single user record.

    Attributes:
        id: UUID primary key of the user.
        org_id: UUID of the owning organization.
        email: User email address.
        role: Current RBAC role.
        created_at: UTC timestamp of user creation.
    """

    id: uuid.UUID = Field(description="UUID primary key of the user.")
    org_id: uuid.UUID = Field(description="UUID of the owning organization.")
    email: str = Field(description="User email address.")
    role: str = Field(description="Current RBAC role.")
    created_at: datetime.datetime = Field(description="UTC timestamp of user creation.")

    model_config = {"from_attributes": True}


class UserListResponse(BaseModel):
    """Paginated list of users.

    Attributes:
        items: List of :class:`UserResponse` records.
        total: Total count of users in the org.
    """

    items: list[UserResponse] = Field(default_factory=list)
    total: int = Field(description="Total number of users in the org.")
