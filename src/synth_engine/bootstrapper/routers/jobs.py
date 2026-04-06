"""FastAPI router for Jobs CRUD and lifecycle endpoints.

Implements CRUD for :class:`SynthesisJob` resources plus lifecycle routes:
    - ``GET /jobs``: list jobs with cursor-based pagination.
    - ``GET /jobs/{id}``: get a single job.
    - ``POST /jobs``: create a new job in QUEUED status.
    - ``POST /jobs/{id}/start``: enqueue the Huey synthesis task.
    - ``POST /jobs/{id}/shred``: NIST 800-88 compliant artifact erasure.

Streaming endpoints are in :mod:`synth_engine.bootstrapper.routers.jobs_streaming`:
    - ``GET /jobs/{id}/stream``: Server-Sent Events progress stream.
    - ``GET /jobs/{id}/download``: streams the synthetic Parquet artifact.

Cursor-based pagination uses the integer ``id`` column as the cursor.
Pattern: ``GET /jobs?after=<cursor>&limit=20`` returns jobs where
``id > cursor``, ordered by ``id`` ascending.

Authorization (T39.2, P79):
    All resource endpoints filter by ``org_id`` from the verified JWT claim
    (via :func:`~synth_engine.bootstrapper.dependencies.tenant.get_current_user`).
    Accessing a resource owned by a different organization returns 404 Not Found
    (not 403 Forbidden) to prevent resource enumeration.

All 404 and error responses use RFC 7807 Problem Details format via
:func:`synth_engine.bootstrapper.errors.problem_detail`.

Task: P5-T5.1 — Task Orchestration API Core
Task: T39.2 — Add Authorization & IDOR Protection on All Resource Endpoints
Task: T62.1 — Wrap Database Commits in Exception Handlers
Task: T71.1 — Add audit events to unaudited destructive endpoints
Task: T71.5 — Use shared AUDIT_WRITE_FAILURE_TOTAL counter
Task: P79-T79.2 — Migrate routers to TenantContext (org_id filtering)
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlmodel import Session, col, select

from synth_engine.bootstrapper.dependencies.db import get_db_session
from synth_engine.bootstrapper.dependencies.tenant import TenantContext, get_current_user
from synth_engine.bootstrapper.errors import problem_detail
from synth_engine.bootstrapper.openapi_metadata import (
    COMMON_ERROR_RESPONSES,
    CONFLICT_ERROR_RESPONSES,
)
from synth_engine.bootstrapper.schemas.jobs import (
    JobCreateRequest,
    JobListResponse,
    JobResponse,
)
from synth_engine.modules.synthesizer.jobs.job_models import SynthesisJob
from synth_engine.modules.synthesizer.jobs.tasks import run_synthesis_job
from synth_engine.modules.synthesizer.lifecycle.shred import shred_artifacts
from synth_engine.shared.observability import AUDIT_WRITE_FAILURE_TOTAL
from synth_engine.shared.security.audit import get_audit_logger
from synth_engine.shared.telemetry import inject_trace_context

_logger = logging.getLogger(__name__)

router = APIRouter(prefix="/jobs", tags=["jobs"])

#: Default page size for cursor-based pagination.
_DEFAULT_PAGE_SIZE: int = 20

#: Maximum page size a caller may request.
_MAX_PAGE_SIZE: int = 100

#: Job status that permits the shred operation.
_SHRED_ELIGIBLE_STATUS: str = "COMPLETE"

#: Job status applied after successful artifact erasure.
_SHREDDED_STATUS: str = "SHREDDED"

# T71.5 — Use shared AUDIT_WRITE_FAILURE_TOTAL counter from shared/observability.py.


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _persist_new_job(session: Session, job: SynthesisJob, org_id: str) -> JSONResponse | None:
    """Commit the new job row; return a 409 or 500 response on DB error.

    Args:
        session: Open SQLModel Session with ``job`` already added.
        job: The SynthesisJob to persist.
        org_id: Organization ID (for logging).

    Returns:
        None on success; a 409/500 JSONResponse on DB error.
    """
    try:
        session.commit()
        session.refresh(job)
        return None
    except IntegrityError:
        session.rollback()
        _logger.warning("create_job: IntegrityError for org=%s", org_id, exc_info=True)
        return JSONResponse(
            status_code=409,
            content={
                "type": "about:blank",
                "title": "Conflict",
                "status": 409,
                "detail": "A resource with these properties already exists.",
            },
        )
    except SQLAlchemyError:
        session.rollback()
        _logger.warning("create_job: SQLAlchemyError for org=%s", org_id, exc_info=True)
        return JSONResponse(
            status_code=500,
            content={
                "type": "about:blank",
                "title": "Internal Server Error",
                "status": 500,
                "detail": "Database operation failed. Please retry.",
            },
        )


def _write_shred_audit(
    audit: object, user_id: str, job_id: int, table_name: str, org_id: str
) -> JSONResponse | None:
    """Write the pre-shred ARTIFACT_SHREDDED audit event (T70.8).

    Returns a 500 JSONResponse if the audit write fails, or None on success.
    No artifact deletion occurs if this returns a non-None response.

    Args:
        audit: The AuditLogger instance.
        user_id: The authenticated user ID.
        job_id: The job's integer PK.
        table_name: The job's table name (for audit details).
        org_id: The organization ID.

    Returns:
        500 JSONResponse on audit failure; None on success.
    """
    try:
        audit.log_event(  # type: ignore[attr-defined]
            event_type="ARTIFACT_SHREDDED",
            actor=user_id,
            resource=f"synthesis_job/{job_id}",
            action="shred",
            details={"job_id": str(job_id), "table_name": table_name, "org_id": org_id},
        )
        return None
    except (ValueError, OSError, UnicodeError):
        AUDIT_WRITE_FAILURE_TOTAL.labels(router="jobs", endpoint="/jobs/{job_id}/shred").inc()
        _logger.exception(
            "Job %d: WORM audit log failed before artifact shredding — aborting (T70.8)", job_id
        )
        return JSONResponse(
            status_code=500,
            content=problem_detail(
                status=500,
                title="Internal Server Error",
                detail="Audit write failed. Artifact shred was NOT performed.",
            ),
        )


def _shred_and_compensate(
    audit: object, user_id: str, job_id: int, job: SynthesisJob, org_id: str
) -> JSONResponse | None:
    """Run shred_artifacts(); emit a compensating event on OSError (T70.8).

    Returns a 500 JSONResponse on OSError, or None on success.

    Args:
        audit: The AuditLogger instance (for the compensating event).
        user_id: The authenticated user ID.
        job_id: The job's integer PK.
        job: The SynthesisJob ORM object.
        org_id: The organization ID.

    Returns:
        500 JSONResponse on OSError; None on success.
    """
    try:
        shred_artifacts(job)
        return None
    except OSError as exc:
        _logger.error("Job %d: artifact erasure failed: %s", job_id, exc.__class__.__name__)
        try:
            audit.log_event(  # type: ignore[attr-defined]
                event_type="ARTIFACT_SHRED_FAILED",
                actor=user_id,
                resource=f"synthesis_job/{job_id}",
                action="shred",
                details={"job_id": str(job_id), "error": exc.__class__.__name__, "org_id": org_id},
            )
        except (ValueError, OSError, UnicodeError):
            _logger.exception(
                "Job %d: compensating audit event ARTIFACT_SHRED_FAILED also failed", job_id
            )
        return JSONResponse(
            status_code=500,
            content=problem_detail(
                status=500,
                title="Internal Server Error",
                detail="Artifact erasure failed due to an I/O error — see server logs.",
            ),
        )


def _commit_shredded_status(session: Session, job: SynthesisJob, job_id: int) -> JSONResponse:
    """Persist SHREDDED status, commit, and return the 200 or 500 response (T62.1).

    Commits BEFORE returning 200 to avoid confirming a shred that was never
    recorded in the database (T62.1 critical fix).

    Args:
        session: The active database session.
        job: The SynthesisJob ORM object.
        job_id: The job's integer PK (for logging).

    Returns:
        200 JSONResponse on commit success; 500 on SQLAlchemyError.
    """
    job.status = _SHREDDED_STATUS
    session.add(job)
    try:
        session.commit()
    except SQLAlchemyError:
        session.rollback()
        _logger.warning(
            "shred_job: SQLAlchemyError persisting SHREDDED status for job_id=%d",
            job_id,
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
    _logger.info("Job %d: artifacts shredded, status set to SHREDDED.", job_id)
    return JSONResponse(
        status_code=200,
        content={"status": _SHREDDED_STATUS, "job_id": job_id},
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get(
    "",
    response_model=JobListResponse,
    summary="List synthesis jobs",
    description=(
        "Return all synthesis jobs owned by the authenticated organization "
        "with cursor-based pagination."
    ),
    responses=COMMON_ERROR_RESPONSES,
)
def list_jobs(
    session: Annotated[Session, Depends(get_db_session)],
    current_user: Annotated[TenantContext, Depends(get_current_user)],
    after: int | None = Query(default=None, description="Cursor: return jobs with id > after"),
    limit: int = Query(default=_DEFAULT_PAGE_SIZE, ge=1, le=_MAX_PAGE_SIZE),
) -> JobListResponse:
    """List synthesis jobs with cursor-based pagination.

    Only returns jobs scoped to the authenticated organization (IDOR protection).

    Args:
        session: Database session (injected by FastAPI DI).
        current_user: Resolved tenant identity (org_id, user_id, role) from JWT.
        after: Integer cursor -- only return jobs with ``id > after``.
        limit: Maximum number of results to return (default 20, max 100).

    Returns:
        :class:`JobListResponse` with a list of jobs and an optional
        ``next_cursor`` for fetching the next page.
    """
    query = (
        select(SynthesisJob)
        .where(SynthesisJob.org_id == current_user.org_id)
        .order_by(col(SynthesisJob.id))
    )
    if after is not None:
        query = query.where(col(SynthesisJob.id) > after)
    query = query.limit(limit + 1)  # fetch one extra to determine next_cursor

    jobs = session.exec(query).all()

    next_cursor: int | None = None
    if len(jobs) > limit:
        next_cursor = jobs[limit - 1].id
        jobs = jobs[:limit]

    return JobListResponse(
        items=[JobResponse.model_validate(j) for j in jobs],
        next_cursor=next_cursor,
    )


@router.post(
    "",
    response_model=JobResponse,
    status_code=201,
    summary="Create a synthesis job",
    description="Create a new synthesis job in QUEUED status. Call POST /{id}/start to enqueue.",
    responses=COMMON_ERROR_RESPONSES,
)
def create_job(
    body: JobCreateRequest,
    session: Annotated[Session, Depends(get_db_session)],
    current_user: Annotated[TenantContext, Depends(get_current_user)],
) -> JobResponse | JSONResponse:
    """Create a new synthesis job in QUEUED status.

    The job is persisted but NOT enqueued.  Call ``POST /jobs/{id}/start`` to
    begin training.  ``owner_id`` is set from the user's JWT sub claim.
    ``org_id`` is set from the organization's JWT org_id claim.

    Args:
        body: Job creation request payload.
        session: Database session (injected by FastAPI DI).
        current_user: Resolved tenant identity (org_id, user_id, role) from JWT.

    Returns:
        The newly created :class:`JobResponse`, RFC 7807 409/500 on DB error.
    """
    job = SynthesisJob(
        table_name=body.table_name,
        parquet_path=body.parquet_path,
        total_epochs=body.total_epochs,
        num_rows=body.num_rows,
        checkpoint_every_n=body.checkpoint_every_n,
        enable_dp=body.enable_dp,
        noise_multiplier=body.noise_multiplier,
        max_grad_norm=body.max_grad_norm,
        owner_id=current_user.user_id,
        org_id=current_user.org_id,
    )
    session.add(job)
    err = _persist_new_job(session, job, current_user.org_id)
    if err is not None:
        return err
    return JobResponse.model_validate(job)


@router.get(
    "/{job_id}",
    response_model=JobResponse,
    summary="Get a synthesis job",
    description=(
        "Return a single synthesis job by ID. "
        "Returns 404 if not found or owned by another organization."
    ),
    responses=COMMON_ERROR_RESPONSES,
)
def get_job(
    job_id: int,
    session: Annotated[Session, Depends(get_db_session)],
    current_user: Annotated[TenantContext, Depends(get_current_user)],
) -> JobResponse | JSONResponse:
    """Get a synthesis job by ID.

    Returns 404 if the job does not exist **or** belongs to a different
    organization (IDOR protection — 404 prevents resource enumeration).

    Args:
        job_id: The integer primary key of the job.
        session: Database session (injected by FastAPI DI).
        current_user: Resolved tenant identity (org_id, user_id, role) from JWT.

    Returns:
        :class:`JobResponse` on success, or RFC 7807 404 on not found
        or org mismatch.
    """
    job = session.get(SynthesisJob, job_id)
    if job is None or job.org_id != current_user.org_id:
        return JSONResponse(
            status_code=404,
            content=problem_detail(
                status=404,
                title="Not Found",
                detail=f"SynthesisJob with id={job_id} not found.",
            ),
        )
    return JobResponse.model_validate(job)


@router.post(
    "/{job_id}/start",
    summary="Start a synthesis job",
    description=(
        "Enqueue the synthesis job for processing. Transitions the job from QUEUED to RUNNING."
    ),
    responses=CONFLICT_ERROR_RESPONSES,
    status_code=202,
)
def start_job(
    job_id: int,
    session: Annotated[Session, Depends(get_db_session)],
    current_user: Annotated[TenantContext, Depends(get_current_user)],
) -> JSONResponse:
    """Enqueue a synthesis job for background processing.

    Looks up the :class:`SynthesisJob` by ``job_id``, then calls
    ``run_synthesis_job(job_id)`` to enqueue the Huey task.  Returns
    ``202 Accepted`` immediately -- the job runs asynchronously.

    Returns 404 if the job does not exist **or** belongs to a different
    organization (IDOR protection).

    Args:
        job_id: The integer primary key of the job to start.
        session: Database session (injected by FastAPI DI).
        current_user: Resolved tenant identity (org_id, user_id, role) from JWT.

    Returns:
        ``{"status": "accepted", "job_id": <id>}`` with HTTP 202, or
        RFC 7807 404 if the job does not exist or org mismatch.
    """
    job = session.get(SynthesisJob, job_id)
    if job is None or job.org_id != current_user.org_id:
        return JSONResponse(
            status_code=404,
            content=problem_detail(
                status=404,
                title="Not Found",
                detail=f"SynthesisJob with id={job_id} not found.",
            ),
        )

    # run_synthesis_job enqueues a Huey task synchronously (blocking call that
    # pushes a message onto the task queue).  FastAPI runs sync route handlers
    # in a threadpool, so this blocking enqueue does not stall the event loop.
    # T25.2 AC1-AC2: Inject the current span context into the carrier dict and
    # pass it to the Huey task so the worker can re-attach the trace.
    run_synthesis_job(job_id, trace_carrier=inject_trace_context())
    _logger.info("Enqueued synthesis job %d.", job_id)
    return JSONResponse(
        status_code=202,
        content={"status": "accepted", "job_id": job_id},
    )


@router.post(
    "/{job_id}/shred",
    summary="Shred job artifacts",
    description=(
        "Permanently delete the synthetic Parquet artifact using NIST 800-88 compliant erasure."
    ),
    responses=COMMON_ERROR_RESPONSES,
    status_code=200,
)
def shred_job(
    job_id: int,
    session: Annotated[Session, Depends(get_db_session)],
    current_user: Annotated[TenantContext, Depends(get_current_user)],
) -> JSONResponse:
    """Shred all synthesis artifacts for a COMPLETE job (NIST SP 800-88).

    Deletes Parquet output, HMAC sidecar, and model pickle.  Only COMPLETE
    jobs belonging to the authenticated organization are eligible.  Returns 404
    for any non-eligible job (IDOR protection).

    Audit ordering (T70.8): audit event written BEFORE artifact deletion;
    compensating event on shred failure.  Commit BEFORE 200 (T62.1).

    Args:
        job_id: The integer primary key of the job to shred.
        session: Database session (injected by FastAPI DI).
        current_user: Resolved tenant identity (org_id, user_id, role) from JWT.

    Returns:
        200 on success; 404 if ineligible; 500 on audit/shred/commit failure.
    """
    job = session.get(SynthesisJob, job_id)
    if job is None or job.org_id != current_user.org_id or job.status != _SHRED_ELIGIBLE_STATUS:
        return JSONResponse(
            status_code=404,
            content=problem_detail(
                status=404,
                title="Not Found",
                detail=(
                    f"SynthesisJob with id={job_id} not found or not eligible for shredding. "
                    f"Only jobs with status=COMPLETE may be shredded."
                ),
            ),
        )

    audit = get_audit_logger()
    audit_err = _write_shred_audit(
        audit, current_user.user_id, job_id, job.table_name, current_user.org_id
    )
    if audit_err is not None:
        return audit_err

    shred_err = _shred_and_compensate(audit, current_user.user_id, job_id, job, current_user.org_id)
    if shred_err is not None:
        return shred_err

    return _commit_shredded_status(session, job, job_id)
