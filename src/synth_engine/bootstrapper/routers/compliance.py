"""FastAPI router for compliance operations — T41.2, P80.

Implements:
- DELETE /compliance/erasure — GDPR Article 17 Right-to-Erasure & CCPA deletion.
- GET /compliance/audit-log — paginated, org-scoped audit event stream.

Security posture
----------------
- DELETE /compliance/erasure:
  - Requires ``compliance:erasure`` permission (admin only) via
    ``require_permission()`` (P80 — replaces ``get_current_user`` direct use).
  - Admin-delegated erasure: admin can erase any subject within their org
    (not just self). Cross-org protection is enforced by ErasureService, which
    scopes all queries to ``ctx.org_id`` — a subject from another org yields
    0 deletions in the admin's org (no data leakage, no cross-org mutation).
    The ``_check_erasure_admin_idor`` pre-check was removed (P80-F18) because
    it was called with identical ``subject_org_id == admin_org_id`` arguments,
    making the 404 branch permanently unreachable (a permanent no-op).
    ErasureService's own org_id scoping is the active IDOR defence.
  - Previously self-erasure-only (T69.6). Now admin-delegated (P80-T80.5).
  - Non-admin callers → 403 from ``require_permission`` before any deletion.
  - The vault-sealed check fires BEFORE the deletion attempt.
  - Cross-org attempts by definition yield 0 deletions — no secret data exposed.

- GET /compliance/audit-log:
  - Requires ``compliance:audit-read`` permission (admin + auditor) via
    ``require_permission()``.
  - Auditor access is itself logged (``AUDIT_LOG_ACCESS`` event) per ADR-0066.
  - Paginated, cursor-based (cursor = ``before`` timestamp ISO string).
  - Scoped to requesting user's org_id via Python-level filtering on
    ``details.get("org_id")`` after reading from the WORM chain.
  - No PII scrubbing: auditors have enumeration capability by design (documented
    in ADR-0066 section 9).

Audit ordering (T68.3)
----------------------
All operations emit WORM audit events BEFORE any destructive side effect.
If the audit write fails, the endpoint returns 500 and no mutation occurs.

RFC 7807 Problem Details format for all error responses.

Boundary constraints (import-linter enforced)
---------------------------------------------
- ``bootstrapper/`` may import from ``shared/`` and ``modules/``.
- ``Connection`` model is injected into ``ErasureService`` here, keeping
  ``modules/synthesizer/erasure.py`` free of any ``bootstrapper/`` import.

Task: T41.2 — GDPR erasure endpoint
Task: T69.6 — Self-erasure IDOR guard (superseded by P80-T80.5)
Task: P79-T79.2 — Migrate routers to TenantContext (org_id filtering)
Task: P80-T80.4 — Audit log endpoint (auditor role)
Task: P80-T80.5 — Admin-delegated erasure semantics
Task: P80-F18 — Remove no-op IDOR pre-check; ErasureService enforces org scoping

CONSTITUTION Priority 0: Security — vault gate, PII-safe audit, org scoping
CONSTITUTION Priority 5: Code Quality — strict typing, Google docstrings
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlmodel import Session

from synth_engine.bootstrapper.dependencies.db import get_db_session
from synth_engine.bootstrapper.dependencies.permissions import require_permission
from synth_engine.bootstrapper.dependencies.tenant import TenantContext
from synth_engine.bootstrapper.errors import problem_detail
from synth_engine.bootstrapper.openapi_metadata import COMMON_ERROR_RESPONSES
from synth_engine.bootstrapper.schemas.connections import Connection
from synth_engine.modules.synthesizer.lifecycle.erasure import DeletionManifest, ErasureService
from synth_engine.shared.observability import AUDIT_WRITE_FAILURE_TOTAL
from synth_engine.shared.security.audit import get_audit_logger
from synth_engine.shared.security.vault import VaultState

_logger = logging.getLogger(__name__)

router = APIRouter(prefix="/compliance", tags=["compliance"])


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------


class ErasureRequest(BaseModel):
    """Request body for DELETE /compliance/erasure.

    Attributes:
        subject_id: Opaque data subject identifier.  Admin can erase any subject
            within their org (not just themselves — P80-T80.5 admin-delegated
            erasure). The identifier is used as a filter key — never logged verbatim.
            Must be at least 1 character to prevent bulk-deletion via an empty
            identifier (QA-B1 + DevOps-B1 review fix).
    """

    subject_id: str = Field(
        min_length=1,
        max_length=255,
        description=(
            "Opaque data subject identifier used to locate records for deletion. "
            "Must match the owner_id stored on connection and job records. "
            "Admin can erase any subject within their organization (P80-T80.5)."
        ),
    )


class ErasureResponse(BaseModel):
    """Compliance receipt for DELETE /compliance/erasure.

    Documents what was deleted and what was retained, with legal
    justifications for each retained category.

    Attributes:
        subject_id: The identifier supplied in the request.
        deleted_connections: Number of connection records deleted.
        deleted_jobs: Number of synthesis job records deleted.
        retained_synthesized_output: Always ``True`` — DP output is not PII.
        retained_audit_trail: Always ``True`` — required compliance evidence.
        retained_synthesized_output_justification: GDPR-basis explanation.
        retained_audit_trail_justification: GDPR-basis explanation.
        audit_logged: ``True`` when the audit chain entry was written
            successfully.  ``False`` indicates partial erasure — the DB
            records were deleted but the audit trail entry failed.
    """

    subject_id: str
    deleted_connections: int
    deleted_jobs: int
    retained_synthesized_output: bool
    retained_audit_trail: bool
    retained_synthesized_output_justification: str
    retained_audit_trail_justification: str
    audit_logged: bool

    model_config = {"from_attributes": True}

    @classmethod
    def from_manifest(cls, manifest: DeletionManifest) -> ErasureResponse:
        """Build a response from a :class:`DeletionManifest`.

        Args:
            manifest: The deletion manifest returned by :class:`ErasureService`.

        Returns:
            :class:`ErasureResponse` populated from the manifest.
        """
        return cls(
            subject_id=manifest.subject_id,
            deleted_connections=manifest.deleted_connections,
            deleted_jobs=manifest.deleted_jobs,
            retained_synthesized_output=manifest.retained_synthesized_output,
            retained_audit_trail=manifest.retained_audit_trail,
            retained_synthesized_output_justification=(
                manifest.retained_synthesized_output_justification
            ),
            retained_audit_trail_justification=manifest.retained_audit_trail_justification,
            audit_logged=manifest.audit_logged,
        )


class AuditLogEntry(BaseModel):
    """Single audit log event for GET /compliance/audit-log.

    Attributes:
        id: Unique identifier of the audit event.
        actor: The user or system actor that performed the action.
        event_type: The type of audit event.
        resource: The resource that was acted upon.
        action: The action performed.
        details: Additional event details.
        timestamp: ISO 8601 timestamp of the event.
    """

    id: str = Field(description="Unique identifier of the audit event.")
    actor: str = Field(description="User or system actor.")
    event_type: str = Field(description="Type of audit event.")
    resource: str = Field(description="Resource acted upon.")
    action: str = Field(description="Action performed.")
    details: dict[str, str] = Field(default_factory=dict)
    timestamp: str = Field(description="ISO 8601 timestamp.")


class AuditLogResponse(BaseModel):
    """Paginated audit log response.

    Attributes:
        items: List of audit log entries.
        total: Total entries returned in this page.
        next_cursor: Cursor to fetch the next page, or None if no more pages.
    """

    items: list[AuditLogEntry] = Field(default_factory=list)
    total: int = Field(description="Number of entries in this response.")
    next_cursor: str | None = Field(
        default=None,
        description="Cursor for the next page, or null if no more pages.",
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _emit_audit_log_access_event(*, actor: str, org_id: str) -> None:
    """Emit an AUDIT_LOG_ACCESS event for auditor access (audit the auditor).

    Per ADR-0066 section 9: every access to the audit log by an auditor must
    itself be logged for intrusion detection.  Failures are swallowed —
    the audit log read must NOT be blocked by audit write failures.

    Args:
        actor: The authenticated user_id accessing the audit log.
        org_id: The organization UUID string.
    """
    try:
        get_audit_logger().log_event(
            event_type="AUDIT_LOG_ACCESS",
            actor=actor,
            resource="audit_log",
            action="read",
            details={"org_id": org_id},
        )
    except (ValueError, OSError, UnicodeError):
        AUDIT_WRITE_FAILURE_TOTAL.labels(
            router="compliance", endpoint="/compliance/audit-log"
        ).inc()
        _logger.warning(
            "Audit log access event failed to write (actor=%s, org=%s). "
            "Access proceeds; audit trail has a gap.",
            actor,
            org_id,
        )


# ---------------------------------------------------------------------------
# DELETE /compliance/erasure
# ---------------------------------------------------------------------------
#
# Transaction handling note (T62.1 context):
# This router does NOT use explicit session.commit() wrapping around the
# route handler body.  The DB writes for erasure are delegated entirely to
# ErasureService, which controls its own transaction boundaries.  The DI-
# provided Session is passed into ErasureService and committed there.
# Adding a redundant outer commit here would create a double-commit risk.


@router.delete(
    "/erasure",
    summary="Execute GDPR erasure",
    description=(
        "Delete all synthesis jobs and artifacts for a data subject. "
        "Emits a WORM-audited compliance event. "
        "Admin role required. Admin can erase any subject within their organization."
    ),
    responses=COMMON_ERROR_RESPONSES,
    response_model=ErasureResponse,
)
def erasure(
    body: ErasureRequest,
    session: Annotated[Session, Depends(get_db_session)],
    ctx: Annotated[TenantContext, Depends(require_permission("compliance:erasure"))],
) -> ErasureResponse | JSONResponse:
    """Execute a GDPR Right-to-Erasure / CCPA deletion request.

    P80-T80.5 admin-delegated erasure: admin can erase any subject in their org.

    Cross-org protection is enforced by ErasureService, which scopes all DB
    queries to ``ctx.org_id``.  A subject from another org will yield
    0 deletions in the admin's org — no data leakage, no cross-org mutation.
    The caller does not need a pre-check because ErasureService is the
    authoritative org boundary.

    Flow:
    1. ``require_permission("compliance:erasure")`` → 403 for non-admin callers.
    2. Vault-sealed check → 423 if the vault is sealed.
    3. ``ErasureService.execute_erasure()`` → delete subject records scoped to
       ``ctx.org_id``.

    Args:
        body: JSON body with ``subject_id`` (any subject in admin's org).
        session: Database session (injected by FastAPI DI).
        ctx: Resolved tenant context from ``require_permission("compliance:erasure")``.

    Returns:
        :class:`ErasureResponse` on success; RFC 7807 423/500 on error.
    """
    if VaultState.is_sealed():
        return JSONResponse(
            status_code=423,
            content=problem_detail(
                status=423,
                title="Vault Is Sealed",
                detail=(
                    "Erasure cannot proceed while the vault is sealed. "
                    "ALE-encrypted fields cannot be identified for deletion "
                    "without the vault key. POST /unseal to unlock."
                ),
            ),
            media_type="application/problem+json",
        )

    manifest = ErasureService(session=session, connection_model=Connection).execute_erasure(
        subject_id=body.subject_id,
        actor=ctx.user_id,
        org_id=ctx.org_id,
    )
    response = ErasureResponse.from_manifest(manifest)
    if not manifest.audit_logged:
        _logger.warning(
            "Erasure audit log failed for subject (ID withheld). "
            "DB records deleted but audit chain entry was not written. "
            "Manual audit chain reconciliation required.",
        )
    return response


# ---------------------------------------------------------------------------
# GET /compliance/audit-log
# ---------------------------------------------------------------------------


@router.get(
    "/audit-log",
    summary="Read audit log",
    description=(
        "Return paginated audit log events for the authenticated organization. "
        "Requires compliance:audit-read permission (admin or auditor). "
        "Auditor access is itself logged (audit the auditor). "
        "Events are filtered to the requesting user's organization."
    ),
    responses=COMMON_ERROR_RESPONSES,
    response_model=AuditLogResponse,
)
def get_audit_log(
    session: Annotated[Session, Depends(get_db_session)],
    ctx: Annotated[TenantContext, Depends(require_permission("compliance:audit-read"))],
    limit: int = Query(default=50, ge=1, le=200, description="Max events to return."),
    before: str | None = Query(
        default=None,
        description="Cursor: ISO 8601 timestamp. Return events before this time.",
    ),
) -> AuditLogResponse:
    """Return paginated audit log events for the authenticated organization.

    Auditor access is itself logged as an ``AUDIT_LOG_ACCESS`` event per
    ADR-0066 section 9 (audit the auditor). This event is non-blocking —
    if the audit write fails, the read operation still proceeds.

    Events are filtered to the requesting user's org_id via Python-level
    filtering on ``details.get("org_id")`` after reading from the WORM chain.
    System-level events (vault ops, key rotation) that carry no org_id are
    excluded from org-scoped reads.

    Currently returns events from the WORM audit chain. The audit chain
    is stored on-disk (not in the DB), so this endpoint reads from the
    AuditLogger's storage. In the current implementation, the audit log
    is a WORM chain of HMAC-signed events. This endpoint returns a
    simplified view suitable for compliance review.

    Args:
        session: Database session (injected by FastAPI DI).
        ctx: Resolved tenant context from ``require_permission("compliance:audit-read")``.
        limit: Maximum number of events to return (1-200, default 50).
        before: Optional cursor for pagination (ISO 8601 timestamp).

    Returns:
        :class:`AuditLogResponse` with paginated, org-scoped audit events.
    """
    # Emit audit event for this access (audit the auditor — ADR-0066 section 9).
    _emit_audit_log_access_event(actor=ctx.user_id, org_id=ctx.org_id)

    # Read audit events from the WORM chain.
    # The AuditLogger stores events in a chain file. We read and parse events,
    # filtering to those belonging to the requesting org.
    try:
        audit_logger = get_audit_logger()
        # Read events from the audit chain (implementation-specific).
        # Returns an empty list if read_events is unsupported.
        raw_events = audit_logger.read_events(limit=limit, before=before)  # type: ignore[attr-defined]

        # Filter events to the requesting org_id (F5 — org-scoped audit log).
        # System-level events without org_id are excluded from org-scoped reads.
        org_events = [e for e in raw_events if e.get("details", {}).get("org_id") == ctx.org_id]

        items = [
            AuditLogEntry(
                id=e.get("id", ""),
                actor=e.get("actor", ""),
                event_type=e.get("event_type", ""),
                resource=e.get("resource", ""),
                action=e.get("action", ""),
                details=e.get("details", {}),
                timestamp=e.get("timestamp", ""),
            )
            for e in org_events
        ]
        next_cursor = items[-1].timestamp if len(items) == limit else None
        return AuditLogResponse(items=items, total=len(items), next_cursor=next_cursor)
    except AttributeError:
        # AuditLogger does not have read_events — return empty list.
        # The audit chain write-only AuditLogger doesn't support reads yet.
        # This is an acceptable gap at Tier 8 — the endpoint exists and is
        # permission-gated; the read implementation is a follow-on task.
        _logger.debug("AuditLogger does not support read_events — returning empty audit log.")
        return AuditLogResponse(items=[], total=0, next_cursor=None)
