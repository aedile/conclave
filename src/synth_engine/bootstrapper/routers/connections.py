"""FastAPI router for Connections endpoints.

Implements CRUD for :class:`Connection` database connection configuration
resources.

All 404 responses use RFC 7807 Problem Details format.

Authorization (T39.2, P79):
    All resource endpoints filter by ``org_id`` from the verified JWT claim
    (via :func:`~synth_engine.bootstrapper.dependencies.tenant.get_current_user`).
    Accessing a resource owned by a different organization returns 404 Not Found
    (not 403 Forbidden) to prevent resource enumeration.

    ``org_id`` is derived exclusively from the verified JWT — HTTP headers
    (e.g., ``X-Org-ID``) are intentionally ignored (ATTACK-02 mitigation).

Audit before mutation (T71.1):
    DELETE emits a ``CONNECTION_DELETED`` WORM audit event BEFORE the database
    delete.  If the audit write fails, the endpoint returns 500 and no deletion
    occurs.  If the DB commit fails after a successful audit, a compensating
    ``CONNECTION_DELETE_ABORTED`` event is emitted.

Task: P5-T5.1 — Task Orchestration API Core
Task: T39.2 — Add Authorization & IDOR Protection on All Resource Endpoints
Task: T62.1 — Wrap Database Commits in Exception Handlers
Task: T71.1 — Add audit events to unaudited destructive endpoints
Task: T71.5 — Use shared AUDIT_WRITE_FAILURE_TOTAL counter
Task: P79-T79.2 — Migrate routers to TenantContext (org_id filtering)
Task: P80-T80.2 — Annotate endpoints with require_permission()
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Response
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlmodel import Session, select

from synth_engine.bootstrapper.dependencies.db import get_db_session
from synth_engine.bootstrapper.dependencies.permissions import require_permission
from synth_engine.bootstrapper.dependencies.tenant import TenantContext
from synth_engine.bootstrapper.errors import problem_detail
from synth_engine.bootstrapper.openapi_metadata import COMMON_ERROR_RESPONSES
from synth_engine.bootstrapper.schemas.connections import (
    Connection,
    ConnectionCreateRequest,
    ConnectionListResponse,
    ConnectionResponse,
)
from synth_engine.shared.observability import AUDIT_WRITE_FAILURE_TOTAL
from synth_engine.shared.security.audit import get_audit_logger

_logger = logging.getLogger(__name__)

router = APIRouter(prefix="/connections", tags=["connections"])

#: Default page size for listing connections.
_DEFAULT_PAGE_SIZE: int = 20


@router.get(
    "",
    response_model=ConnectionListResponse,
    summary="List database connections",
    description="Return all stored database connections owned by the authenticated organization.",
    responses=COMMON_ERROR_RESPONSES,
)
def list_connections(
    session: Annotated[Session, Depends(get_db_session)],
    current_user: Annotated[TenantContext, Depends(require_permission("connections:read"))],
) -> ConnectionListResponse:
    """List all stored database connections owned by the authenticated organization.

    Only returns connections scoped to the authenticated org (IDOR protection).

    Args:
        session: Database session (injected by FastAPI DI).
        current_user: Resolved tenant identity (org_id, user_id, role) from JWT.

    Returns:
        :class:`ConnectionListResponse` with up to 100 connections owned by the
        org (hard limit prevents unbounded DB reads, P59 Red-team F4).
    """
    connections = session.exec(
        select(Connection).where(Connection.org_id == current_user.org_id).limit(100)
    ).all()
    return ConnectionListResponse(
        items=[ConnectionResponse.model_validate(c) for c in connections],
        next_cursor=None,
    )


@router.post(
    "",
    response_model=ConnectionResponse,
    status_code=201,
    summary="Create a database connection",
    description=(
        "Store a new database connection configuration. "
        "Credentials are encrypted at rest using AES-256-GCM."
    ),
    responses=COMMON_ERROR_RESPONSES,
)
def create_connection(
    body: ConnectionCreateRequest,
    session: Annotated[Session, Depends(get_db_session)],
    current_user: Annotated[TenantContext, Depends(require_permission("connections:create"))],
) -> ConnectionResponse | JSONResponse:
    """Create a new database connection configuration.

    The ``owner_id`` is set from the authenticated user's ``user_id`` claim.
    The ``org_id`` is set from the authenticated organization's JWT ``org_id`` claim.

    Args:
        body: Connection creation request payload.
        session: Database session (injected by FastAPI DI).
        current_user: Resolved tenant identity (org_id, user_id, role) from JWT.

    Returns:
        The newly created :class:`ConnectionResponse`, RFC 7807 409 on
        constraint violation, or RFC 7807 500 on other database errors.
    """
    conn = Connection(
        name=body.name,
        host=body.host,
        port=body.port,
        database=body.database,
        schema_name=body.schema_name,
        owner_id=current_user.user_id,
        org_id=current_user.org_id,
    )
    session.add(conn)
    try:
        session.commit()
        session.refresh(conn)
    except IntegrityError:
        session.rollback()
        _logger.warning(
            "create_connection: IntegrityError for org=%s user=%s",
            current_user.org_id,
            current_user.user_id,
            exc_info=True,
        )
        return JSONResponse(
            status_code=409,
            content={
                "type": "about:blank",
                "title": "Conflict",
                "status": 409,
                "detail": "A resource with these properties already exists.",
            },
        )
    except SQLAlchemyError:
        session.rollback()
        _logger.warning(
            "create_connection: SQLAlchemyError for org=%s user=%s",
            current_user.org_id,
            current_user.user_id,
            exc_info=True,
        )
        return JSONResponse(
            status_code=500,
            content={
                "type": "about:blank",
                "title": "Internal Server Error",
                "status": 500,
                "detail": "Database operation failed. Please retry.",
            },
        )
    return ConnectionResponse.model_validate(conn)


@router.get(
    "/{connection_id}",
    summary="Get a database connection",
    description=(
        "Return a single database connection by ID. "
        "Returns 404 if not found or owned by another organization."
    ),
    responses=COMMON_ERROR_RESPONSES,
    response_model=ConnectionResponse,
)
def get_connection(
    connection_id: Annotated[str, Path(max_length=255)],
    session: Annotated[Session, Depends(get_db_session)],
    current_user: Annotated[TenantContext, Depends(require_permission("connections:read"))],
) -> ConnectionResponse | JSONResponse:
    """Get a database connection by ID.

    Returns 404 if the connection does not exist **or** belongs to a
    different organization (IDOR protection — 404 prevents enumeration).

    Args:
        connection_id: String UUID primary key of the connection.
        session: Database session (injected by FastAPI DI).
        current_user: Resolved tenant identity (org_id, user_id, role) from JWT.

    Returns:
        :class:`ConnectionResponse` on success, or RFC 7807 404 on not found
        or org mismatch.
    """
    conn = session.get(Connection, connection_id)
    if conn is None or conn.org_id != current_user.org_id:
        return JSONResponse(
            status_code=404,
            content=problem_detail(
                status=404,
                title="Not Found",
                detail=f"Connection with id={connection_id} not found.",
            ),
        )
    return ConnectionResponse.model_validate(conn)


@router.delete(
    "/{connection_id}",
    summary="Delete a database connection",
    description="Permanently delete a database connection configuration.",
    responses=COMMON_ERROR_RESPONSES,
    status_code=204,
)
def delete_connection(
    connection_id: Annotated[str, Path(max_length=255)],
    session: Annotated[Session, Depends(get_db_session)],
    current_user: Annotated[TenantContext, Depends(require_permission("connections:delete"))],
) -> Response:
    """Delete a database connection by ID.

    Emits a ``CONNECTION_DELETED`` WORM audit event BEFORE the database delete
    (T71.1 audit-before-mutation).  If the audit write fails, returns 500 and
    the connection is NOT deleted.  If the DB commit fails after a successful
    audit, a compensating ``CONNECTION_DELETE_ABORTED`` event is emitted.

    Returns 404 if the connection does not exist **or** belongs to a
    different organization (IDOR protection — 404 prevents enumeration).

    Args:
        connection_id: String UUID primary key of the connection to delete.
        session: Database session (injected by FastAPI DI).
        current_user: Resolved tenant identity (org_id, user_id, role) from JWT.

    Returns:
        HTTP 204 No Content on success, RFC 7807 404 on not found or
        org mismatch, RFC 7807 500 on audit failure (no mutation), or
        RFC 7807 500 on database errors.
    """
    conn = session.get(Connection, connection_id)
    if conn is None or conn.org_id != current_user.org_id:
        return JSONResponse(
            status_code=404,
            content=problem_detail(
                status=404,
                title="Not Found",
                detail=f"Connection with id={connection_id} not found.",
            ),
        )

    # T71.1: Emit audit event BEFORE the database delete.
    # If the audit write fails, return 500 and do NOT delete.
    try:
        audit = get_audit_logger()
        audit.log_event(
            event_type="CONNECTION_DELETED",
            actor=current_user.user_id,
            resource=f"connection/{connection_id}",
            action="delete",
            details={"connection_id": connection_id, "org_id": current_user.org_id},
        )
    except (ValueError, OSError, UnicodeError):
        AUDIT_WRITE_FAILURE_TOTAL.labels(router="connections", endpoint="/connections/{id}").inc()
        _logger.exception(
            "Audit logging failed for delete_connection id=%s; aborting (T71.1)",
            connection_id,
        )
        return JSONResponse(
            status_code=500,
            content={
                "type": "about:blank",
                "title": "Internal Server Error",
                "status": 500,
                "detail": "Audit write failed. Connection was NOT deleted.",
            },
        )

    # Audit succeeded — now perform the database delete.
    session.delete(conn)
    try:
        session.commit()
    except SQLAlchemyError:
        session.rollback()
        _logger.warning(
            "delete_connection: SQLAlchemyError for connection_id=%s org=%s user=%s",
            connection_id,
            current_user.org_id,
            current_user.user_id,
            exc_info=True,
        )
        # T71.1: Emit compensating event when DB fails after successful audit.
        try:
            audit.log_event(
                event_type="CONNECTION_DELETE_ABORTED",
                actor=current_user.user_id,
                resource=f"connection/{connection_id}",
                action="delete",
                details={
                    "connection_id": connection_id,
                    "org_id": current_user.org_id,
                    "reason": "db_commit_failed",
                },
            )
        except (ValueError, OSError, UnicodeError):
            _logger.exception(
                "delete_connection: compensating audit event CONNECTION_DELETE_ABORTED "
                "also failed for connection_id=%s",
                connection_id,
            )
        return JSONResponse(
            status_code=500,
            content={
                "type": "about:blank",
                "title": "Internal Server Error",
                "status": 500,
                "detail": "Database operation failed. Please retry.",
            },
        )
    return Response(status_code=204)
