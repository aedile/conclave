"""SQLModel table and Pydantic schemas for database Connection resources.

Connections represent database connection configurations used as sources
for ingestion.  They live in the bootstrapper (API layer) because they are
API resources, not domain objects owned by a specific module.

Security note: ``host``, ``database``, and ``schema_name`` are stored
encrypted at rest using the ALE (Application-Level Encryption)
``EncryptedString`` TypeDecorator.  The ``port`` field is a plain integer
and is not sensitive.  Decryption is transparent through the SQLAlchemy
ORM; the API layer always works with plaintext strings.

Task: P5-T5.1 — Task Orchestration API Core
Task: T39.4 — Encrypt Connection Metadata with ALE
CONSTITUTION Priority 0: Security — sensitive fields encrypted at rest
"""

from __future__ import annotations

import uuid

from pydantic import BaseModel
from sqlalchemy import Column
from sqlmodel import Field, SQLModel

from synth_engine.shared.security.ale import EncryptedString


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

    Sensitive fields (``host``, ``database``, ``schema_name``) are
    encrypted at rest via the ALE ``EncryptedString`` TypeDecorator.
    The ORM transparently encrypts on write and decrypts on read; callers
    always receive and supply plaintext strings.

    Note: This class extends ``SQLModel`` directly rather than
    ``shared.db.BaseModel``.  That is intentional — ``Connection`` is an
    API-layer resource (operator-visible configuration), not a domain entity.
    It does not need UUID audit timestamps from ``BaseModel``; its primary key
    is a plain string UUID for SQLite/PostgreSQL portability.

    Attributes:
        id: UUID v4 primary key (stored as VARCHAR string).
        name: Human-readable display name.
        host: Database hostname or IP address (encrypted at rest).
        port: Database port number (plain integer, not sensitive).
        database: Database name to connect to (encrypted at rest).
        schema_name: Schema within the database (encrypted at rest,
            default: ``"public"``).
    """

    __tablename__ = "connection"

    id: str = Field(default_factory=_uuid_str, primary_key=True)
    name: str = Field(..., index=True)
    host: str = Field(sa_column=Column(EncryptedString(), nullable=False))
    port: int
    database: str = Field(sa_column=Column(EncryptedString(), nullable=False))
    schema_name: str = Field(
        default="public",
        sa_column=Column(EncryptedString(), nullable=False, server_default="public"),
    )


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
        host: Database hostname (decrypted).
        port: Database port.
        database: Database name (decrypted).
        schema_name: Schema name (decrypted).
    """

    id: str
    name: str
    host: str
    port: int
    database: str
    schema_name: str

    model_config = {"from_attributes": True}


class ConnectionListResponse(BaseModel):
    """Paginated list response for GET /connections.

    Attributes:
        items: List of connection objects.
        next_cursor: String UUID cursor for next page (None if last page).
    """

    items: list[ConnectionResponse]
    next_cursor: str | None
