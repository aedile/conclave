"""FastAPI router for administrative operations.

Implements:
- PATCH /admin/jobs/{id}/legal-hold — toggle the legal hold flag on a job.

Security posture:
- Ownership-scoped: only the authenticated operator whose org owns the job may
  toggle its legal hold flag.  Requests from any other org return 404 (not 403)
  to avoid leaking the existence of resources owned by other orgs.
- Audit events are emitted BEFORE the database commit (T68.3).  If the audit
  write fails, the request returns 500 and the database change is not applied.
- Every toggle emits a ``LEGAL_HOLD_SET`` or ``LEGAL_HOLD_CLEARED`` WORM audit
  event so the hold history is attributable and tamper-evident.

RFC 7807 Problem Details format is used for all error responses.

Boundary constraints (import-linter enforced):
    - ``bootstrapper/`` may import from ``shared/`` and ``modules/``.

CONSTITUTION Priority 0: Security — audit every privilege operation
CONSTITUTION Priority 5: Code Quality — strict typing, Google docstrings
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session

from synth_engine.bootstrapper.dependencies.db import get_db_session
from synth_engine.bootstrapper.dependencies.tenant import TenantContext, get_current_user
from synth_engine.bootstrapper.errors import problem_detail
from synth_engine.bootstrapper.openapi_metadata import COMMON_ERROR_RESPONSES
from synth_engine.modules.synthesizer.jobs.job_models import SynthesisJob
from synth_engine.shared.observability import AUDIT_WRITE_FAILURE_TOTAL
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
# Helpers
# ---------------------------------------------------------------------------


def _check_job_ownership(
    job: SynthesisJob | None,
    job_id: int,
    current_user: TenantContext,
) -> JSONResponse | None:
    """Return a 404 JSONResponse if the job is absent or owned by a different org.

    T68.2: Ownership check — emit WARNING for intrusion detection, return 404
    to avoid leaking the existence of other orgs' resources.

    Args:
        job: The fetched SynthesisJob, or None if not found.
        job_id: Integer PK (for response body and logging).
        current_user: Authenticated TenantContext carrying org_id.

    Returns:
        A 404 JSONResponse if the job is missing or belongs to a different org;
        else None.
    """
    not_found_body = problem_detail(
        status=404,
        title="Not Found",
        detail=f"SynthesisJob with id={job_id} not found.",
    )
    if job is None:
        return JSONResponse(status_code=404, content=not_found_body)
    if job.org_id != current_user.org_id:
        _logger.warning(
            "set_legal_hold: user=%s (org=%s) attempted to access job id=%d "
            "owned by org=%s (IDOR attempt detected)",
            current_user.user_id,
            current_user.org_id,
            job_id,
            job.org_id,
        )
        return JSONResponse(status_code=404, content=not_found_body)
    return None


def _commit_legal_hold_change(
    session: Session, job: SynthesisJob, job_id: int, operator: str, enable: bool
) -> JSONResponse | None:
    """Persist the legal hold flag change; return 500 on SQLAlchemyError.

    Args:
        session: Open SQLModel Session.
        job: The SynthesisJob row to update.
        job_id: Integer PK (for logging).
        operator: Authenticated user identity string (for logging).
        enable: New legal hold value.

    Returns:
        None on success; a 500 JSONResponse on DB failure.
    """
    job.legal_hold = enable
    session.add(job)
    try:
        session.commit()
        session.refresh(job)
        return None
    except SQLAlchemyError:
        session.rollback()
        _logger.warning(
            "set_legal_hold: SQLAlchemyError for job_id=%d operator=%s",
            job_id,
            operator,
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


def _audit_and_commit_legal_hold(
    session: Session,
    job: SynthesisJob,
    job_id: int,
    operator: str,
    enable: bool,
    previous: bool,
) -> JSONResponse | None:
    """Emit audit event then commit legal hold change (T68.3 audit-before-commit).

    Args:
        session: Open SQLModel Session.
        job: The SynthesisJob row to update.
        job_id: Integer PK (for audit and logging).
        operator: Authenticated user identity string.
        enable: New legal hold value to set.
        previous: The previous legal hold value (for audit record).

    Returns:
        None on success; a 500 JSONResponse on audit or DB failure.
    """
    event_type = "LEGAL_HOLD_SET" if enable else "LEGAL_HOLD_CLEARED"
    try:
        get_audit_logger().log_event(
            event_type=event_type,
            actor=operator,
            resource=f"synthesis_job/{job_id}",
            action="legal_hold",
            details={"job_id": str(job_id), "enable": str(enable), "previous": str(previous)},
        )
    except (ValueError, OSError, UnicodeError):
        AUDIT_WRITE_FAILURE_TOTAL.labels(
            router="admin", endpoint="/admin/jobs/{job_id}/legal-hold"
        ).inc()
        _logger.exception(
            "Audit logging failed for legal hold toggle on job id=%d; aborting", job_id
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
    return _commit_legal_hold_change(session, job, job_id, operator, enable)


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
    current_user: Annotated[TenantContext, Depends(get_current_user)],
) -> LegalHoldResponse | JSONResponse:
    """Toggle the legal hold flag on a synthesis job.

    Args:
        job_id: Integer primary key of the job to update.
        body: JSON body with a single boolean ``enable`` field.
        session: Database session (injected by FastAPI DI).
        current_user: Resolved tenant identity (org_id, user_id, role) from JWT.

    Returns:
        :class:`LegalHoldResponse` on success, RFC 7807 404 or 500 on failure.
    """
    job = session.get(SynthesisJob, job_id)
    ownership_err = _check_job_ownership(job, job_id, current_user)
    if ownership_err is not None:
        return ownership_err

    if job is None:  # narrowed by _check_job_ownership — unreachable in practice
        return JSONResponse(
            status_code=404,
            content=problem_detail(
                status=404,
                title="Not Found",
                detail=f"SynthesisJob with id={job_id} not found.",
            ),
        )
    previous = job.legal_hold
    err = _audit_and_commit_legal_hold(
        session, job, job_id, current_user.user_id, body.enable, previous
    )
    if err is not None:
        return err

    _logger.info(
        "Legal hold %s for job id=%d (was=%s, now=%s)",
        "set" if body.enable else "cleared",
        job_id,
        previous,
        job.legal_hold,
    )
    return LegalHoldResponse(job_id=job_id, legal_hold=job.legal_hold)
