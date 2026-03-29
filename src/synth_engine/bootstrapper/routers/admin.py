"""FastAPI router for administrative operations — T41.1.

Implements:
- PATCH /admin/jobs/{id}/legal-hold — toggle the legal hold flag on a job.

The legal hold flag (``SynthesisJob.legal_hold``) prevents a job from being
deleted by the routine data retention cleanup task regardless of how old the
record is.  This endpoint is the sole authoritative way to set or clear the
flag.

Security posture:
- The endpoint enforces ownership-scoping: only the authenticated operator who
  owns the job may toggle its legal hold flag.  Requests from any other operator
  return 404 (not 403) to avoid leaking the existence of resources owned by
  other operators.  This matches the IDOR protection pattern applied to
  /jobs and /connections (T39.2, T68.2).
- An audit WARNING is logged when an operator attempts to access another
  operator's job, for intrusion-detection purposes (T68.2 spec amendment).
- The request payload contains only a boolean (``enable``); no PII is accepted
  or returned.
- Audit events are emitted BEFORE the database commit so that no destructive
  operation proceeds without a successful audit trail (T68.3).  If the audit
  write fails, the request returns 500 and the database change is rolled back.
- Every toggle emits a ``LEGAL_HOLD_SET`` or ``LEGAL_HOLD_CLEARED`` WORM audit
  event so the hold history is attributable and tamper-evident.

RFC 7807 Problem Details format is used for all error responses.

Boundary constraints (import-linter enforced):
    - ``bootstrapper/`` may import from ``shared/`` and ``modules/``.

CONSTITUTION Priority 0: Security — audit every privilege operation
CONSTITUTION Priority 5: Code Quality — strict typing, Google docstrings
Task: T41.1 — Implement Data Retention Policy
Task: T62.1 — Wrap Database Commits in Exception Handlers
Task: T68.2 — RBAC Guard on Admin Endpoints (ownership-scoped)
Task: T68.3 — Mandatory Audit Before Destructive Operations
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from prometheus_client import Counter
from pydantic import BaseModel, Field
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session

from synth_engine.bootstrapper.dependencies.auth import get_current_operator
from synth_engine.bootstrapper.dependencies.db import get_db_session
from synth_engine.bootstrapper.errors import problem_detail
from synth_engine.bootstrapper.openapi_metadata import COMMON_ERROR_RESPONSES
from synth_engine.modules.synthesizer.jobs.job_models import SynthesisJob
from synth_engine.shared.security.audit import get_audit_logger

_logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])

# ---------------------------------------------------------------------------
# T70.9 — Prometheus counter for audit-write failures in admin router.
# Uses a static endpoint label to keep Prometheus cardinality bounded.
# ---------------------------------------------------------------------------
AUDIT_WRITE_FAILURE_TOTAL: Counter = Counter(
    "audit_write_failure_total_admin",
    "Audit write failures in admin router",
    ["endpoint"],
)


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------


class LegalHoldRequest(BaseModel):
    """Request body for PATCH /admin/jobs/{id}/legal-hold.

    Attributes:
        enable: ``True`` to set legal hold; ``False`` to clear it.
    """

    enable: bool = Field(description="True to set legal hold; False to clear it.")


class LegalHoldResponse(BaseModel):
    """Response body for PATCH /admin/jobs/{id}/legal-hold.

    Attributes:
        job_id: Integer primary key of the affected job.
        legal_hold: The new value of the legal hold flag after the update.
    """

    job_id: int = Field(description="Integer primary key of the affected job.")
    legal_hold: bool = Field(description="New value of the legal hold flag.")


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------


@router.patch(
    "/jobs/{job_id}/legal-hold",
    summary="Set legal hold on a job",
    description=(
        "Apply or remove a legal hold flag on a synthesis job to prevent data retention cleanup."
    ),
    responses=COMMON_ERROR_RESPONSES,
    response_model=LegalHoldResponse,
)
def set_legal_hold(
    job_id: int,
    body: LegalHoldRequest,
    session: Annotated[Session, Depends(get_db_session)],
    current_operator: Annotated[str, Depends(get_current_operator)],
) -> LegalHoldResponse | JSONResponse:
    """Toggle the legal hold flag on a synthesis job.

    Setting ``legal_hold=True`` prevents the job from being deleted by the
    routine retention cleanup task regardless of the configured
    ``JOB_RETENTION_DAYS`` TTL.  Setting it to ``False`` re-enables normal
    TTL-based deletion.

    Ownership check (T68.2): the job's ``owner_id`` must match the authenticated
    operator's ``sub`` claim.  A mismatch returns 404 rather than 403, so
    that the existence of other operators' resources is not leaked.

    Audit before commit (T68.3): the WORM audit event is emitted before the
    database commit.  If the audit write fails, the endpoint returns 500 and
    the database change is rolled back.

    Security:
        Ownership-scoped — only the authenticated operator owning the job can
        toggle legal hold on it.  A WARNING is logged when an operator attempts
        to access another operator's job (for intrusion-detection purposes).

    Args:
        job_id: Integer primary key of the job to update.
        body: JSON body with a single boolean ``enable`` field.
        session: Database session (injected by FastAPI DI).
        current_operator: Authenticated operator sub claim (injected by FastAPI DI).

    Returns:
        :class:`LegalHoldResponse` with the updated ``legal_hold`` value on
        success, RFC 7807 404 if the job does not exist or is owned by another
        operator, RFC 7807 500 on audit failure or database error.
    """
    job = session.get(SynthesisJob, job_id)
    if job is None:
        return JSONResponse(
            status_code=404,
            content=problem_detail(
                status=404,
                title="Not Found",
                detail=f"SynthesisJob with id={job_id} not found.",
            ),
        )

    # T68.2: Ownership check — emit WARNING for intrusion detection, return 404 to
    # avoid leaking the existence of other operators' resources.
    if job.owner_id != current_operator:
        _logger.warning(
            "set_legal_hold: operator=%s attempted to access job id=%d owned by operator=%s "
            "(IDOR attempt detected)",
            current_operator,
            job_id,
            job.owner_id,
        )
        return JSONResponse(
            status_code=404,
            content=problem_detail(
                status=404,
                title="Not Found",
                detail=f"SynthesisJob with id={job_id} not found.",
            ),
        )

    previous = job.legal_hold
    event_type = "LEGAL_HOLD_SET" if body.enable else "LEGAL_HOLD_CLEARED"

    # T68.3: Emit audit event BEFORE the database commit.
    # If the audit write fails (any exception), return 500 and do NOT commit.
    # This ensures no destructive operation proceeds without a successful audit trail.
    try:
        get_audit_logger().log_event(
            event_type=event_type,
            actor=current_operator,
            resource=f"synthesis_job/{job_id}",
            action="legal_hold",
            details={
                "job_id": str(job_id),
                "enable": str(body.enable),
                "previous": str(previous),
            },
        )
    except Exception:
        AUDIT_WRITE_FAILURE_TOTAL.labels(endpoint="/admin/jobs/{job_id}/legal-hold").inc()
        _logger.exception(
            "Audit logging failed for legal hold toggle on job id=%d; aborting (T68.3)",
            job_id,
        )
        return JSONResponse(
            status_code=500,
            content={
                "type": "about:blank",
                "title": "Internal Server Error",
                "status": 500,
                "detail": "Audit write failed. Legal hold change was not applied.",
            },
        )

    # Audit succeeded — now commit the database change.
    job.legal_hold = body.enable
    session.add(job)
    try:
        session.commit()
        session.refresh(job)
    except SQLAlchemyError:
        session.rollback()
        _logger.warning(
            "set_legal_hold: SQLAlchemyError for job_id=%d operator=%s",
            job_id,
            current_operator,
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

    _logger.info(
        "Legal hold %s for job id=%d (was=%s, now=%s)",
        "set" if body.enable else "cleared",
        job_id,
        previous,
        job.legal_hold,
    )

    return LegalHoldResponse(job_id=job_id, legal_hold=job.legal_hold)
