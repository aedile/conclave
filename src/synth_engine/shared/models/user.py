"""SQLModel table definition for the User entity.

A User belongs to exactly one Organization via ``org_id`` FK.
The ``role`` field controls RBAC permissions (admin/operator/viewer/auditor).
User records are created automatically when an org is provisioned, and during
migration 009 backfill for existing single-operator deployments.

Default user
------------
Migration 009 seeds a default user with UUID ``00000000-0000-0000-0000-000000000001``
in the default organization.  Pass-through mode (no JWT secret) returns this
sentinel in :func:`~synth_engine.bootstrapper.dependencies.tenant.get_current_user`.

Import boundaries
-----------------
This module lives in ``shared/`` because User is consumed by:
- ``bootstrapper/dependencies/tenant.py`` (JWT sub → user lookup)
- ``bootstrapper/routers/`` (RBAC checks)
- All modules that carry ``user_id`` audit fields

Must NOT import from any module-specific package.

CONSTITUTION Priority 0: Security — identity anchor for all audit events
CONSTITUTION Priority 5: Code Quality — strict typing, Google docstrings
Phase: 79 — Multi-Tenancy Foundation (T79.1)
"""

from __future__ import annotations

import uuid

from sqlmodel import Field

from synth_engine.shared.db import BaseModel


class User(BaseModel, table=True):
    """Database table for tenant users.

    Each row represents one authenticated user within an organization.
    Users are identified by their ``id`` in JWT ``sub`` claims.

    Attributes:
        id: UUID v4 primary key (from BaseModel), auto-generated.
        org_id: FK reference to the owning Organization model
            (:class:`~synth_engine.shared.models.organization.Organization`).
        email: User's email address (unique within org, used for display).
        role: RBAC role string.  One of: ``admin``, ``operator``, ``viewer``,
            ``auditor``.  Defaults to ``operator``.
        created_at: UTC timestamp of user creation (from BaseModel).
        updated_at: UTC timestamp of last update (from BaseModel).
    """

    __tablename__ = "users"

    org_id: uuid.UUID = Field(foreign_key="organizations.id", index=True)
    email: str = Field(..., index=True)
    role: str = Field(default="operator")
