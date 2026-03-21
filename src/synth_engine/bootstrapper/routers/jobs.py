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
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from sqlmodel import Session, col, select

from synth_engine.bootstrapper.dependencies.auth import get_current_operator
from synth_engine.bootstrapper.dependencies.db import get_db_session
from synth_engine.bootstrapper.errors import problem_detail
from synth_engine.bootstrapper.schemas.jobs import (
    JobCreateRequest,
    JobListResponse,
    JobResponse,
)
from synth_engine.modules.synthesizer.job_models import SynthesisJob
from synth_engine.modules.synthesizer.shred import shred_artifacts
from synth_engine.modules.synthesizer.tasks import run_synthesis_job
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


@router.get("", response_model=JobListResponse)
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


@router.post("", response_model=JobResponse, status_code=201)
def create_job(
    body: JobCreateRequest,
    session: Annotated[Session, Depends(get_db_session)],
    current_operator: Annotated[str, Depends(get_current_operator)],
) -> JobResponse:
    """Create a new synthesis job in QUEUED status.

    The job is persisted to the database but NOT yet enqueued.  Call
    ``POST /jobs/{id}/start`` to enqueue and begin training.  The
    ``owner_id`` is set from the authenticated operator's JWT sub claim.

    Args:
        body: Job creation request payload.
        session: Database session (injected by FastAPI DI).
        current_operator: JWT sub claim of the authenticated operator.

    Returns:
        The newly created :class:`JobResponse`.
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
    session.commit()
    session.refresh(job)
    return JobResponse.model_validate(job)


@router.get("/{job_id}", response_model=JobResponse)
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


@router.post("/{job_id}/start", status_code=202)
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


@router.post("/{job_id}/shred", status_code=200)
def shred_job(
    job_id: int,
    session: Annotated[Session, Depends(get_db_session)],
    current_operator: Annotated[str, Depends(get_current_operator)],
) -> JSONResponse:
    """Shred all synthesis artifacts for a COMPLETE job (NIST SP 800-88).

    Deletes the generated Parquet output, its HMAC-SHA256 signature sidecar,
    and the trained model artifact pickle from the filesystem.  Emits a WORM
    audit event and transitions the job status to ``SHREDDED``.

    Only jobs in ``COMPLETE`` status **owned by the authenticated operator**
    are eligible.  Jobs in any other status, owned by a different operator,
    or already-``SHREDDED`` jobs return 404 Problem Detail response.

    Args:
        job_id: The integer primary key of the job to shred.
        session: Database session (injected by FastAPI DI).
        current_operator: JWT sub claim of the authenticated operator.

    Returns:
        ``{"status": "SHREDDED", "job_id": <id>}`` with HTTP 200 on success,
        RFC 7807 404 if the job does not exist, is not eligible, or ownership
        mismatch, or RFC 7807 500 if an ``OSError`` prevents artifact deletion.
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

    # Delegate physical file deletion to the domain function.
    # This follows the pattern from T22.4: routers delegate to domain services.
    try:
        shred_artifacts(job)
    except OSError as exc:
        # Log with basename only — never full path (security mandate T23.1/T23.2).
        _logger.error("Job %d: artifact erasure failed: %s", job_id, exc.__class__.__name__)
        return JSONResponse(
            status_code=500,
            content=problem_detail(
                status=500,
                title="Internal Server Error",
                detail=("Artifact erasure failed due to an I/O error — see server logs."),
            ),
        )

    # Emit WORM audit event (CONSTITUTION Priority 0: Security).
    # Must be called AFTER deletion so the event records what was accomplished.
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
    except Exception:  # Broad catch intentional: audit failure must not block status update
        # Audit log failure must NOT prevent the status transition --
        # the files are already deleted; aborting here would leave the
        # record in COMPLETE with no artifacts.
        _logger.exception("Job %d: WORM audit log failed after artifact shredding.", job_id)

    job.status = _SHREDDED_STATUS
    session.add(job)
    session.commit()

    _logger.info("Job %d: artifacts shredded, status set to SHREDDED.", job_id)
    return JSONResponse(
        status_code=200,
        content={"status": _SHREDDED_STATUS, "job_id": job_id},
    )
