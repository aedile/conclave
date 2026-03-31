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

Authorization (T39.2):
    All resource endpoints filter by ``owner_id`` from the JWT ``sub`` claim.
    Accessing a resource owned by a different operator returns 404 Not Found
    (not 403 Forbidden) to prevent resource enumeration.

All 404 and error responses use RFC 7807 Problem Details format via
:func:`synth_engine.bootstrapper.errors.problem_detail`.

Task: P5-T5.1 — Task Orchestration API Core
Task: P22-T22.1 — Job Schema DP Parameters
Task: P23-T23.4 — Cryptographic Erasure Endpoint
Task: P26-T26.1 — Split Oversized Files (Refactor Only)
Task: T39.2 — Add Authorization & IDOR Protection on All Resource Endpoints
Task: T62.1 — Wrap Database Commits in Exception Handlers
Task: T70.8 — Audit-before-mutation ordering standardisation
Task: T70.9 — AUDIT_WRITE_FAILURE_TOTAL Prometheus counter
Task: T71.5 — Use shared AUDIT_WRITE_FAILURE_TOTAL counter
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlmodel import Session, col, select

from synth_engine.bootstrapper.dependencies.auth import get_current_operator
from synth_engine.bootstrapper.dependencies.db import get_db_session
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


@router.get(
    "",
    response_model=JobListResponse,
    summary="List synthesis jobs",
    description=(
        "Return all synthesis jobs owned by the authenticated operator "
        "with cursor-based pagination."
    ),
    responses=COMMON_ERROR_RESPONSES,
)
def list_jobs(
    session: Annotated[Session, Depends(get_db_session)],
    current_operator: Annotated[str, Depends(get_current_operator)],
    after: int | None = Query(default=None, description="Cursor: return jobs with id > after"),
    limit: int = Query(default=_DEFAULT_PAGE_SIZE, ge=1, le=_MAX_PAGE_SIZE),
) -> JobListResponse:
    """List synthesis jobs with cursor-based pagination.

    Only returns jobs owned by the authenticated operator (IDOR protection).

    Args:
        session: Database session (injected by FastAPI DI).
        current_operator: JWT sub claim of the authenticated operator.
        after: Integer cursor -- only return jobs with ``id > after``.
        limit: Maximum number of results to return (default 20, max 100).

    Returns:
        :class:`JobListResponse` with a list of jobs and an optional
        ``next_cursor`` for fetching the next page.
    """
    query = (
        select(SynthesisJob)
        .where(SynthesisJob.owner_id == current_operator)
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
    current_operator: Annotated[str, Depends(get_current_operator)],
) -> JobResponse | JSONResponse:
    """Create a new synthesis job in QUEUED status.

    The job is persisted to the database but NOT yet enqueued.  Call
    ``POST /jobs/{id}/start`` to enqueue and begin training.  The
    ``owner_id`` is set from the authenticated operator's JWT sub claim.

    Args:
        body: Job creation request payload.
        session: Database session (injected by FastAPI DI).
        current_operator: JWT sub claim of the authenticated operator.

    Returns:
        The newly created :class:`JobResponse`, RFC 7807 409 on constraint
        violation, or RFC 7807 500 on other database errors.
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
        owner_id=current_operator,
    )
    session.add(job)
    try:
        session.commit()
        session.refresh(job)
    except IntegrityError:
        session.rollback()
        _logger.warning(
            "create_job: IntegrityError for operator=%s", current_operator, exc_info=True
        )
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
        _logger.warning(
            "create_job: SQLAlchemyError for operator=%s", current_operator, exc_info=True
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
    return JobResponse.model_validate(job)


@router.get(
    "/{job_id}",
    response_model=JobResponse,
    summary="Get a synthesis job",
    description=(
        "Return a single synthesis job by ID. "
        "Returns 404 if not found or owned by another operator."
    ),
    responses=COMMON_ERROR_RESPONSES,
)
def get_job(
    job_id: int,
    session: Annotated[Session, Depends(get_db_session)],
    current_operator: Annotated[str, Depends(get_current_operator)],
) -> JobResponse | JSONResponse:
    """Get a synthesis job by ID.

    Returns 404 if the job does not exist **or** is owned by a different
    operator (IDOR protection — 404 prevents resource enumeration).

    Args:
        job_id: The integer primary key of the job.
        session: Database session (injected by FastAPI DI).
        current_operator: JWT sub claim of the authenticated operator.

    Returns:
        :class:`JobResponse` on success, or RFC 7807 404 on not found
        or ownership mismatch.
    """
    job = session.get(SynthesisJob, job_id)
    if job is None or job.owner_id != current_operator:
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
    current_operator: Annotated[str, Depends(get_current_operator)],
) -> JSONResponse:
    """Enqueue a synthesis job for background processing.

    Looks up the :class:`SynthesisJob` by ``job_id``, then calls
    ``run_synthesis_job(job_id)`` to enqueue the Huey task.  Returns
    ``202 Accepted`` immediately -- the job runs asynchronously.

    Returns 404 if the job does not exist **or** is owned by a different
    operator (IDOR protection).

    Args:
        job_id: The integer primary key of the job to start.
        session: Database session (injected by FastAPI DI).
        current_operator: JWT sub claim of the authenticated operator.

    Returns:
        ``{"status": "accepted", "job_id": <id>}`` with HTTP 202, or
        RFC 7807 404 if the job does not exist or ownership mismatch.
    """
    job = session.get(SynthesisJob, job_id)
    if job is None or job.owner_id != current_operator:
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
    current_operator: Annotated[str, Depends(get_current_operator)],
) -> JSONResponse:
    """Shred all synthesis artifacts for a COMPLETE job (NIST SP 800-88).

    Deletes the generated Parquet output, its HMAC-SHA256 signature sidecar,
    and the trained model artifact pickle from the filesystem.  Emits a WORM
    audit event BEFORE artifact deletion (T70.8 — audit-before-mutation
    standardisation).  Transitions the job status to ``SHREDDED``.

    Only jobs in ``COMPLETE`` status **owned by the authenticated operator**
    are eligible.  Jobs in any other status, owned by a different operator,
    or already-``SHREDDED`` jobs return 404 Problem Detail response.

    Audit ordering (T70.8):
        1. Ownership & eligibility check.
        2. Audit write (ARTIFACT_SHREDDED intent) — returns 500 on failure;
           no mutation proceeds without a successful audit trail.
        3. ``shred_artifacts()`` — if this raises after a successful audit,
           a compensating ``ARTIFACT_SHRED_FAILED`` event is emitted and
           500 is returned.
        4. Status update (SHREDDED) + database commit.

    CRITICAL (T62.1): The 200 SHREDDED response is only returned AFTER the
    ``session.commit()`` succeeds.  If the commit fails, the operator receives
    500 instead of a false confirmation.

    Args:
        job_id: The integer primary key of the job to shred.
        session: Database session (injected by FastAPI DI).
        current_operator: JWT sub claim of the authenticated operator.

    Returns:
        ``{"status": "SHREDDED", "job_id": <id>}`` with HTTP 200 on success,
        RFC 7807 404 if the job does not exist, is not eligible, or ownership
        mismatch, RFC 7807 500 if audit write fails (no shred performed),
        RFC 7807 500 if an ``OSError`` prevents artifact deletion,
        or RFC 7807 500 if the database commit fails.
    """
    job = session.get(SynthesisJob, job_id)

    if job is None or job.owner_id != current_operator or job.status != _SHRED_ELIGIBLE_STATUS:
        detail = (
            f"SynthesisJob with id={job_id} not found or not eligible for shredding. "
            f"Only jobs with status=COMPLETE may be shredded."
        )
        return JSONResponse(
            status_code=404,
            content=problem_detail(
                status=404,
                title="Not Found",
                detail=detail,
            ),
        )

    # T70.8: Emit audit event BEFORE artifact deletion.
    # If the audit write fails (any exception), return 500 and do NOT shred.
    # This ensures no destructive operation proceeds without a successful audit trail.
    try:
        audit = get_audit_logger()
        audit.log_event(
            event_type="ARTIFACT_SHREDDED",
            actor=current_operator,
            resource=f"synthesis_job/{job_id}",
            action="shred",
            details={
                "job_id": str(job_id),
                "table_name": job.table_name,
            },
        )
    except (ValueError, OSError):
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

    # Delegate physical file deletion to the domain function.
    # This follows the pattern from T22.4: routers delegate to domain services.
    # T70.8: If shred fails AFTER successful audit, emit a compensating event.
    try:
        shred_artifacts(job)
    except OSError as exc:
        # Log with basename only — never full path (security mandate T23.1/T23.2).
        _logger.error("Job %d: artifact erasure failed: %s", job_id, exc.__class__.__name__)
        # Emit compensating audit event so the audit chain reflects the failure.
        try:
            audit.log_event(
                event_type="ARTIFACT_SHRED_FAILED",
                actor=current_operator,
                resource=f"synthesis_job/{job_id}",
                action="shred",
                details={
                    "job_id": str(job_id),
                    "error": exc.__class__.__name__,
                },
            )
        except (ValueError, OSError):
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

    job.status = _SHREDDED_STATUS
    session.add(job)

    # T62.1 CRITICAL FIX: commit BEFORE returning 200 SHREDDED.
    # The old code returned 200 before the commit — if commit failed, the operator
    # received a confirmed shred that was never recorded in the database.
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
