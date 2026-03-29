"""SQLModel table and Pydantic schemas for webhook registration and delivery resources.

``WebhookRegistration`` stores operator callback URLs and HMAC signing keys.
``WebhookDelivery`` tracks each individual delivery attempt for the audit trail.

Security posture
----------------
- ``signing_key`` is stored in the database but **never** exposed in API
  responses.  Read-only fields use ``exclude=True`` in the response schema.
- ``owner_id`` scopes all CRUD operations to the authenticated operator
  (IDOR protection, T39.2 pattern).
- Max 10 active registrations per operator enforced at POST time.

Boundary constraints (import-linter enforced):
    - bootstrapper/schemas/ may import from shared/ only.
    - Must NOT import from modules/ or any other bootstrapper/ subpackage.

CONSTITUTION Priority 0: Security — signing key never exposed, tenant isolation
CONSTITUTION Priority 5: Code Quality — strict typing, Google docstrings
Task: T45.3 — Implement Webhook Callbacks for Task Completion
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from pydantic import BaseModel, Field, field_validator
from sqlmodel import Field as SqlField
from sqlmodel import SQLModel


def _uuid_str() -> str:
    """Generate a new UUID v4 as a string.

    Used as the default factory for webhook primary keys to ensure
    SQLite/PostgreSQL portability.

    Returns:
        A new UUID v4 in canonical string format.
    """
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# ORM table models
# ---------------------------------------------------------------------------


class WebhookRegistration(SQLModel, table=True):
    """Database table for webhook callback registrations.

    Each row represents an operator's subscription to one or more job
    lifecycle events.  Deliveries are tracked in :class:`WebhookDelivery`.

    Attributes:
        id: UUID v4 primary key (stored as VARCHAR string).
        owner_id: JWT ``sub`` claim of the registering operator.
            Indexed for ownership-scoped queries.
        callback_url: HTTPS URL that receives POST deliveries.
        signing_key: HMAC-SHA256 signing secret (operator-supplied).
            Stored in DB; never returned in API responses.
        events: JSON-serialised list of subscribed event types
            (e.g. ``["job.completed", "job.failed"]``).
        active: Whether this registration is active.
            Set to ``False`` by DELETE /webhooks/{id}.
        created_at: UTC timestamp of registration creation.
    """

    __tablename__ = "webhook_registration"

    id: str = SqlField(default_factory=_uuid_str, primary_key=True)
    owner_id: str = SqlField(default="", index=True)
    callback_url: str = SqlField(...)
    signing_key: str = SqlField(...)
    #: JSON-encoded list of event types (SQLite stores as text)
    events: str = SqlField(default='["job.completed","job.failed"]')
    active: bool = SqlField(default=True)
    created_at: datetime = SqlField(default_factory=lambda: datetime.now(UTC))


class WebhookDelivery(SQLModel, table=True):
    """Database table tracking each webhook delivery attempt.

    Provides the WORM-adjacent delivery audit log.  Each row records one
    HTTP attempt (not one delivery event — retries produce multiple rows).

    Attributes:
        id: UUID v4 primary key.
        registration_id: FK-style reference to :class:`WebhookRegistration`.
            Stored as a plain string; FK constraint not enforced at DB level
            for SQLite compatibility.
        job_id: Integer PK of the ``SynthesisJob`` that triggered this delivery.
        event_type: Event type string (e.g. ``"job.completed"``).
        delivery_id: UUID v4 identifying the logical delivery event
            (shared across retries for deduplication).
        attempt_number: 1-indexed attempt counter (1-3).
        status: ``"PENDING"`` | ``"SUCCESS"`` | ``"FAILED"`` | ``"SKIPPED"``.
        response_code: HTTP status code from the last attempt (``None`` on network error).
        error_message: Error detail on failure (``None`` on success).
        created_at: UTC timestamp of this attempt row creation.
    """

    __tablename__ = "webhook_delivery"

    id: str = SqlField(default_factory=_uuid_str, primary_key=True)
    registration_id: str = SqlField(...)
    job_id: int = SqlField(...)
    event_type: str = SqlField(...)
    delivery_id: str = SqlField(...)
    attempt_number: int = SqlField(default=1)
    status: str = SqlField(default="PENDING")
    response_code: int | None = SqlField(default=None)
    error_message: str | None = SqlField(default=None)
    created_at: datetime = SqlField(default_factory=lambda: datetime.now(UTC))


# ---------------------------------------------------------------------------
# Pydantic request/response schemas
# ---------------------------------------------------------------------------


class WebhookRegistrationRequest(BaseModel):
    """Request body for POST /webhooks.

    Attributes:
        callback_url: The HTTPS URL that will receive POST deliveries.
        signing_key: Operator-supplied HMAC secret.  Minimum 32 characters.
            Write-only — never returned in responses.
        events: List of event types to subscribe to.
            Valid values: ``"job.completed"``, ``"job.failed"``.
    """

    callback_url: str = Field(
        ...,
        max_length=2048,
        description="HTTPS callback URL for delivery. Maximum 2048 characters. T68.6.",
    )
    signing_key: str = Field(
        ...,
        min_length=32,
        max_length=512,
        description=(
            "HMAC-SHA256 signing secret.  Minimum 32 characters, maximum 512 characters.  "
            "Write-only — never returned in responses. T68.6."
        ),
    )
    events: list[str] = Field(
        default_factory=lambda: ["job.completed", "job.failed"],
        description="Event types to subscribe to.",
    )

    @field_validator("events")
    @classmethod
    def validate_events(cls, v: list[str]) -> list[str]:
        """Ensure all event types are valid.

        Args:
            v: List of event type strings.

        Returns:
            The validated list.

        Raises:
            ValueError: If any event type is not recognized.
        """
        valid = {"job.completed", "job.failed"}
        for event in v:
            if event not in valid:
                raise ValueError(f"Unknown event type: {event!r}. Valid: {sorted(valid)}")
        return v


class WebhookRegistrationResponse(BaseModel):
    """Response body for a single WebhookRegistration resource.

    The ``signing_key`` is intentionally absent — it is write-only.

    Attributes:
        id: UUID primary key.
        owner_id: Operator identity.
        callback_url: The registered callback URL.
        events: Subscribed event type list.
        active: Whether this registration is currently active.
        created_at: Registration creation timestamp.
    """

    id: str
    owner_id: str
    callback_url: str
    events: list[str]
    active: bool
    created_at: datetime

    model_config = {"from_attributes": True}

    @classmethod
    def from_orm_model(cls, reg: WebhookRegistration) -> WebhookRegistrationResponse:
        """Construct from a :class:`WebhookRegistration` ORM instance.

        Deserialises the JSON ``events`` field back to a Python list.

        Args:
            reg: ORM instance.

        Returns:
            :class:`WebhookRegistrationResponse` with ``signing_key`` excluded.
        """
        import json as _json

        events: list[str] = _json.loads(reg.events) if isinstance(reg.events, str) else reg.events
        return cls(
            id=reg.id,
            owner_id=reg.owner_id,
            callback_url=reg.callback_url,
            events=events,
            active=reg.active,
            created_at=reg.created_at,
        )


class WebhookRegistrationListResponse(BaseModel):
    """Response body for GET /webhooks.

    Attributes:
        items: List of webhook registration objects (signing_key excluded).
    """

    items: list[WebhookRegistrationResponse]
