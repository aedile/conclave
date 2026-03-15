"""FastAPI router for Connections endpoints.

Implements CRUD for :class:`Connection` database connection configuration
resources.

All 404 responses use RFC 7807 Problem Details format.

Task: P5-T5.1 — Task Orchestration API Core
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Response
from fastapi.responses import JSONResponse
from sqlmodel import Session, select

from synth_engine.bootstrapper.dependencies.db import get_db_session
from synth_engine.bootstrapper.errors import problem_detail
from synth_engine.bootstrapper.schemas.connections import (
    Connection,
    ConnectionCreateRequest,
    ConnectionListResponse,
    ConnectionResponse,
)

router = APIRouter(prefix="/connections", tags=["connections"])

#: Default page size for listing connections.
_DEFAULT_PAGE_SIZE: int = 20


@router.get("", response_model=ConnectionListResponse)
def list_connections(
    session: Annotated[Session, Depends(get_db_session)],
) -> ConnectionListResponse:
    """List all stored database connections.

    Args:
        session: Database session (injected by FastAPI DI).

    Returns:
        :class:`ConnectionListResponse` with all connections.
    """
    connections = session.exec(select(Connection)).all()
    return ConnectionListResponse(
        items=[ConnectionResponse.model_validate(c) for c in connections],
        next_cursor=None,
    )


@router.post("", response_model=ConnectionResponse, status_code=201)
def create_connection(
    body: ConnectionCreateRequest,
    session: Annotated[Session, Depends(get_db_session)],
) -> ConnectionResponse:
    """Create a new database connection configuration.

    Args:
        body: Connection creation request payload.
        session: Database session (injected by FastAPI DI).

    Returns:
        The newly created :class:`ConnectionResponse`.
    """
    conn = Connection(
        name=body.name,
        host=body.host,
        port=body.port,
        database=body.database,
        schema_name=body.schema_name,
    )
    session.add(conn)
    session.commit()
    session.refresh(conn)
    return ConnectionResponse.model_validate(conn)


@router.get("/{connection_id}", response_model=ConnectionResponse)
def get_connection(
    connection_id: str,
    session: Annotated[Session, Depends(get_db_session)],
) -> ConnectionResponse | JSONResponse:
    """Get a database connection by ID.

    Args:
        connection_id: String UUID primary key of the connection.
        session: Database session (injected by FastAPI DI).

    Returns:
        :class:`ConnectionResponse` on success, or RFC 7807 404 on not found.
    """
    conn = session.get(Connection, connection_id)
    if conn is None:
        return JSONResponse(
            status_code=404,
            content=problem_detail(
                status=404,
                title="Not Found",
                detail=f"Connection with id={connection_id} not found.",
            ),
        )
    return ConnectionResponse.model_validate(conn)


@router.delete("/{connection_id}", status_code=204)
def delete_connection(
    connection_id: str,
    session: Annotated[Session, Depends(get_db_session)],
) -> Response:
    """Delete a database connection by ID.

    Args:
        connection_id: String UUID primary key of the connection to delete.
        session: Database session (injected by FastAPI DI).

    Returns:
        HTTP 204 No Content on success, or RFC 7807 404 on not found.
    """
    conn = session.get(Connection, connection_id)
    if conn is None:
        return JSONResponse(
            status_code=404,
            content=problem_detail(
                status=404,
                title="Not Found",
                detail=f"Connection with id={connection_id} not found.",
            ),
        )
    session.delete(conn)
    session.commit()
    return Response(status_code=204)
