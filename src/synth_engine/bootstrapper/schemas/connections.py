"""SQLModel table and Pydantic schemas for database Connection resources.

Connections represent database connection configurations used as sources
for ingestion.  They live in the bootstrapper (API layer) because they are
API resources, not domain objects owned by a specific module.

Authorization (T39.2):

    ``owner_id`` stores the JWT ``sub`` claim of the operator who created the
    connection.  All resource endpoints filter by ``owner_id`` to prevent
    horizontal privilege escalation (IDOR).  Defaults to ``""`` for backward
    compatibility with records created before T39.2.

Security note: ``host``, ``database``, and ``schema_name`` are stored
encrypted at rest using the ALE (Application-Level Encryption)
``EncryptedString`` TypeDecorator.  The ``port`` field is a plain integer
and is not sensitive.  Decryption is transparent through the SQLAlchemy
ORM; the API layer always works with plaintext strings.

Input validation (P59 Red-team F1): ``name``, ``host``, ``database``, and
``schema_name`` are bounded to 255 characters via Pydantic ``Field``
constraints to prevent oversized-input DoS and DB truncation attacks.

Task: P5-T5.1 — Task Orchestration API Core
Task: T39.2 — Add Authorization & IDOR Protection on All Resource Endpoints
Task: T39.4 — Encrypt Connection Metadata with ALE
Task: P59 — Production Readiness v1.0 — input validation hardening
CONSTITUTION Priority 0: Security — sensitive fields encrypted at rest; bounded inputs
"""

from __future__ import annotations

import uuid

from pydantic import BaseModel, Field
from sqlalchemy import Column
from sqlmodel import Field as SqlField
from sqlmodel import SQLModel

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
        host: Database hostname or IP address (ALE-encrypted at rest).
        port: Database port number (plain integer, not sensitive).
        database: Database name to connect to (ALE-encrypted at rest).
        schema_name: Schema within the database (ALE-encrypted at rest,
            default: ``"public"``).
        owner_id: JWT ``sub`` claim of the operator who created this connection.
            Used for IDOR protection — all resource queries filter by this
            field.  Defaults to ``""`` for backward compatibility with
            records created before T39.2.  Indexed for query performance.
    """

    __tablename__ = "connection"

    id: str = SqlField(default_factory=_uuid_str, primary_key=True)
    name: str = SqlField(..., index=True)
    host: str = SqlField(sa_column=Column(EncryptedString(), nullable=False))
    port: int
    database: str = SqlField(sa_column=Column(EncryptedString(), nullable=False))
    schema_name: str = SqlField(
        default="public",
        sa_column=Column(EncryptedString(), nullable=False),
    )
    #: Operator identity for IDOR protection (T39.2). Empty string = legacy/unconfigured.
    owner_id: str = SqlField(default="", index=True)
    #: Tenant organization UUID for multi-tenant isolation (T79.2, ADR-0065).
    #: Defaults to empty string for backward compatibility with pre-P79 rows.
    org_id: str = SqlField(default="", index=True)


class ConnectionCreateRequest(BaseModel):
    """Request body for POST /api/v1/connections.

    All string fields are bounded to 255 characters to prevent oversized-
    input DoS attacks and unintentional DB truncation (P59 Red-team F1).

    Attributes:
        name: Human-readable display name (max 255 chars).
        host: Database hostname or IP (max 255 chars).
        port: Database port.
        database: Database name (max 255 chars).
        schema_name: Schema to use, default "public" (max 255 chars).
    """

    name: str = Field(..., max_length=255)
    host: str = Field(..., max_length=255)
    port: int = Field(..., ge=1, le=65535)
    database: str = Field(..., max_length=255)
    schema_name: str = Field(default="public", max_length=255)


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
    """Paginated list response for GET /api/v1/connections.

    Attributes:
        items: List of connection objects.
        next_cursor: String UUID cursor for next page (None if last page).
    """

    items: list[ConnectionResponse]
    next_cursor: str | None
