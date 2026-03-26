"""SQLModel table and Pydantic schemas for application Settings resources.

Settings represent key-value application configuration persisted to the
database.  They live in the bootstrapper (API layer) since they are API
resources, not module domain objects.

Input validation (P59 Red-team F2): ``SettingUpsertRequest.value`` is bounded
to 10000 characters to prevent oversized-input DoS attacks.

Task: P5-T5.1 — Task Orchestration API Core
Task: P59 — Production Readiness v1.0 — input validation hardening
"""

from __future__ import annotations

from pydantic import BaseModel, Field
from sqlmodel import Field as SqlField
from sqlmodel import SQLModel


class Setting(SQLModel, table=True):
    """Database table for key-value application settings.

    Note: This class extends ``SQLModel`` directly rather than
    ``shared.db.BaseModel``.  That is intentional — ``Setting`` is an
    API-layer resource (operator-visible configuration), not a domain entity.
    Its primary key is the setting key string itself; it does not need the
    UUID primary key or audit timestamps provided by ``BaseModel``.

    Attributes:
        key: Setting key (primary key; unique identifier).
        value: Setting value as a string.
    """

    __tablename__ = "setting"

    key: str = SqlField(primary_key=True)
    value: str


class SettingUpsertRequest(BaseModel):
    """Request body for PUT /api/v1/settings/{key}.

    The ``value`` field is bounded to 10000 characters to prevent
    oversized-input DoS attacks (P59 Red-team F2).

    Attributes:
        value: New string value to store for the key (max 10000 chars).
    """

    value: str = Field(..., max_length=10000)


class SettingResponse(BaseModel):
    """Response body for a single Setting.

    Attributes:
        key: Setting key.
        value: Setting value.
    """

    key: str
    value: str

    model_config = {"from_attributes": True}


class SettingListResponse(BaseModel):
    """List response for GET /api/v1/settings.

    Attributes:
        items: List of setting objects.
    """

    items: list[SettingResponse]
