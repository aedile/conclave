"""FastAPI router for administrative operations — T41.1.

Implements:
- PATCH /admin/jobs/{id}/legal-hold — toggle the legal hold flag on a job.

The legal hold flag (``SynthesisJob.legal_hold``) prevents a job from being
deleted by the routine data retention cleanup task regardless of how old the
record is.  This endpoint is the sole authoritative way to set or clear the
flag.

Security posture:
- The endpoint is a privileged admin action.  In a multi-operator deployment
  it SHOULD be gated behind an elevated role.  In the current single-operator
  model, the same operator credential suffices.
- Admin endpoints are intentionally not ownership-scoped — they operate on
  any job by ID.  In the current single-operator model this is the correct
  behaviour: there is exactly one operator and all jobs belong to the same
  system context.  Multi-operator deployments MUST add role-based access
  control (RBAC) to restrict admin operations to authorised principals only.
  ADV-023 is resolved by this documentation; no code change is required until
  a multi-operator deployment model is adopted.
- The request payload contains only a boolean (``enable``); no PII is accepted
  or returned.
- Every toggle emits a ``LEGAL_HOLD_SET`` or ``LEGAL_HOLD_CLEARED`` WORM audit
  event so the hold history is attributable and tamper-evident.

RFC 7807 Problem Details format is used for all error responses.

Boundary constraints (import-linter enforced):
    - ``bootstrapper/`` may import from ``shared/`` and ``modules/``.

CONSTITUTION Priority 0: Security — audit every privilege operation
CONSTITUTION Priority 5: Code Quality — strict typing, Google docstrings
Task: T41.1 — Implement Data Retention Policy
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
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

    Every invocation emits a WORM audit event recording the toggle so the
    hold history is fully attributable.

    Security:
        This endpoint is not ownership-scoped — any authenticated operator can
        toggle legal hold on any job by ID.  This is intentional for admin
        operations in the current single-operator model (ADV-023).  When a
        multi-operator deployment model is adopted, RBAC restrictions MUST be
        added here.

    Args:
        job_id: Integer primary key of the job to update.
        body: JSON body with a single boolean ``enable`` field.
        session: Database session (injected by FastAPI DI).
        current_operator: Authenticated operator sub claim (injected by FastAPI DI).

    Returns:
        :class:`LegalHoldResponse` with the updated ``legal_hold`` value on
        success, or RFC 7807 404 if the job does not exist.
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

    previous = job.legal_hold
    job.legal_hold = body.enable
    session.add(job)
    session.commit()
    session.refresh(job)

    event_type = "LEGAL_HOLD_SET" if body.enable else "LEGAL_HOLD_CLEARED"
    _logger.info(
        "Legal hold %s for job id=%d (was=%s, now=%s)",
        "set" if body.enable else "cleared",
        job_id,
        previous,
        job.legal_hold,
    )

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
        # Audit failure must never abort the hold toggle — the DB write
        # succeeded; failing here would leave the operator confused.
        _logger.exception("Audit logging failed for legal hold toggle on job id=%d", job_id)

    return LegalHoldResponse(job_id=job_id, legal_hold=job.legal_hold)
