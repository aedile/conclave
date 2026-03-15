"""SQLModel table and Pydantic schemas for application Settings resources.

Settings represent key-value application configuration persisted to the
database.  They live in the bootstrapper (API layer) since they are API
resources, not module domain objects.

Task: P5-T5.1 — Task Orchestration API Core
"""

from __future__ import annotations

from pydantic import BaseModel
from sqlmodel import Field, SQLModel


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

    key: str = Field(primary_key=True)
    value: str


class SettingUpsertRequest(BaseModel):
    """Request body for PUT /settings/{key}.

    Attributes:
        value: New string value to store for the key.
    """

    value: str


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
    """List response for GET /settings.

    Attributes:
        items: List of setting objects.
    """

    items: list[SettingResponse]
