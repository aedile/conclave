"""SQLModel table and Pydantic schemas for database Connection resources.

Connections represent database connection configurations used as sources
for ingestion.  They live in the bootstrapper (API layer) because they are
API resources, not domain objects owned by a specific module.

Authorization (T39.2):

    ``owner_id`` stores the JWT ``sub`` claim of the operator who created the
    connection.  All resource endpoints filter by ``owner_id`` to prevent
    horizontal privilege escalation (IDOR).  Defaults to ``""`` for backward
    compatibility with records created before T39.2.

Task: P5-T5.1 — Task Orchestration API Core
Task: T39.2 — Add Authorization & IDOR Protection on All Resource Endpoints
"""

from __future__ import annotations

import uuid

from pydantic import BaseModel
from sqlmodel import Field, SQLModel


def _uuid_str() -> str:
    """Generate a new UUID v4 as a string.

    Used as the default factory for Connection.id to ensure SQLite
    compatibility (SQLite does not have a native UUID column type;
    storing as TEXT avoids type binding errors).

    Returns:
        A new UUID v4 in canonical string format (e.g. ``"a1b2c3d4-..."}``).
    """
    return str(uuid.uuid4())


class Connection(SQLModel, table=True):
    """Database table for stored connection configurations.

    The primary key is stored as a VARCHAR UUID string for SQLite
    compatibility.  PostgreSQL stores the same column as TEXT.

    Note: This class extends ``SQLModel`` directly rather than
    ``shared.db.BaseModel``.  That is intentional — ``Connection`` is an
    API-layer resource (operator-visible configuration), not a domain entity.
    It does not need UUID audit timestamps from ``BaseModel``; its primary key
    is a plain string UUID for SQLite/PostgreSQL portability.

    Attributes:
        id: UUID v4 primary key (stored as VARCHAR string).
        name: Human-readable display name.
        host: Database hostname or IP address.
        port: Database port number.
        database: Database name to connect to.
        schema_name: Schema within the database (default: public).
        owner_id: JWT ``sub`` claim of the operator who created this connection.
            Used for IDOR protection — all resource queries filter by this
            field.  Defaults to ``""`` for backward compatibility with
            records created before T39.2.  Indexed for query performance.
    """

    __tablename__ = "connection"

    id: str = Field(default_factory=_uuid_str, primary_key=True)
    name: str = Field(..., index=True)
    host: str
    port: int
    database: str
    schema_name: str = Field(default="public")
    #: Operator identity for IDOR protection (T39.2). Empty string = legacy/unconfigured.
    owner_id: str = Field(default="", index=True)


class ConnectionCreateRequest(BaseModel):
    """Request body for POST /connections.

    Attributes:
        name: Human-readable display name.
        host: Database hostname or IP.
        port: Database port.
        database: Database name.
        schema_name: Schema to use (default: public).
    """

    name: str
    host: str
    port: int
    database: str
    schema_name: str = "public"


class ConnectionResponse(BaseModel):
    """Response body for a single Connection resource.

    The ``id`` is returned as a string (UUID format) for JSON serialization.

    Attributes:
        id: UUID primary key as a string.
        name: Display name.
        host: Database hostname.
        port: Database port.
        database: Database name.
        schema_name: Schema name.
        owner_id: Operator identity who owns this connection.
    """

    id: str
    name: str
    host: str
    port: int
    database: str
    schema_name: str
    owner_id: str = ""

    model_config = {"from_attributes": True}


class ConnectionListResponse(BaseModel):
    """Paginated list response for GET /connections.

    Attributes:
        items: List of connection objects.
        next_cursor: String UUID cursor for next page (None if last page).
    """

    items: list[ConnectionResponse]
    next_cursor: str | None
