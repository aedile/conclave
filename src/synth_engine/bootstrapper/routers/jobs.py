"""FastAPI router for Jobs endpoints.

Implements CRUD for :class:`SynthesisJob` resources plus:
    - ``POST /jobs/{id}/start``: enqueues the Huey synthesis task.
    - ``GET /jobs/{id}/stream``: Server-Sent Events progress stream.

Cursor-based pagination uses the integer ``id`` column as the cursor.
Pattern: ``GET /jobs?after=<cursor>&limit=20`` returns jobs where
``id > cursor``, ordered by ``id`` ascending.

All 404 and error responses use RFC 7807 Problem Details format via
:func:`synth_engine.bootstrapper.errors.problem_detail`.

Task: P5-T5.1 — Task Orchestration API Core
Task: P22-T22.1 — Job Schema DP Parameters
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import AsyncGenerator, Generator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from sqlalchemy import Engine
from sqlmodel import Session, col, select
from sse_starlette.sse import EventSourceResponse

from synth_engine.bootstrapper.dependencies.db import get_db_session
from synth_engine.bootstrapper.errors import problem_detail
from synth_engine.bootstrapper.schemas.jobs import (
    JobCreateRequest,
    JobListResponse,
    JobResponse,
)
from synth_engine.bootstrapper.sse import job_event_stream
from synth_engine.modules.synthesizer.job_models import SynthesisJob
from synth_engine.modules.synthesizer.tasks import run_synthesis_job
from synth_engine.shared.db import SessionFactory

_logger = logging.getLogger(__name__)

router = APIRouter(prefix="/jobs", tags=["jobs"])

#: Default page size for cursor-based pagination.
_DEFAULT_PAGE_SIZE: int = 20

#: Maximum page size a caller may request.
_MAX_PAGE_SIZE: int = 100

#: SSE polling interval injected for testing (seconds).
_SSE_POLL_INTERVAL: float = 1.0


@router.get("", response_model=JobListResponse)
def list_jobs(
    session: Annotated[Session, Depends(get_db_session)],
    after: int | None = Query(default=None, description="Cursor: return jobs with id > after"),
    limit: int = Query(default=_DEFAULT_PAGE_SIZE, ge=1, le=_MAX_PAGE_SIZE),
) -> JobListResponse:
    """List synthesis jobs with cursor-based pagination.

    Args:
        session: Database session (injected by FastAPI DI).
        after: Integer cursor -- only return jobs with ``id > after``.
        limit: Maximum number of results to return (default 20, max 100).

    Returns:
        :class:`JobListResponse` with a list of jobs and an optional
        ``next_cursor`` for fetching the next page.
    """
    query = select(SynthesisJob).order_by(col(SynthesisJob.id))
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
) -> JobResponse:
    """Create a new synthesis job in QUEUED status.

    The job is persisted to the database but NOT yet enqueued.  Call
    ``POST /jobs/{id}/start`` to enqueue and begin training.

    Args:
        body: Job creation request payload.
        session: Database session (injected by FastAPI DI).

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
    )
    session.add(job)
    session.commit()
    session.refresh(job)
    return JobResponse.model_validate(job)


@router.get("/{job_id}", response_model=JobResponse)
def get_job(
    job_id: int,
    session: Annotated[Session, Depends(get_db_session)],
) -> JobResponse | JSONResponse:
    """Get a synthesis job by ID.

    Args:
        job_id: The integer primary key of the job.
        session: Database session (injected by FastAPI DI).

    Returns:
        :class:`JobResponse` on success, or RFC 7807 404 on not found.
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
    return JobResponse.model_validate(job)


@router.post("/{job_id}/start", status_code=202)
def start_job(
    job_id: int,
    session: Annotated[Session, Depends(get_db_session)],
) -> JSONResponse:
    """Enqueue a synthesis job for background processing.

    Looks up the :class:`SynthesisJob` by ``job_id``, then calls
    ``run_synthesis_job(job_id)`` to enqueue the Huey task.  Returns
    ``202 Accepted`` immediately -- the job runs asynchronously.

    Args:
        job_id: The integer primary key of the job to start.
        session: Database session (injected by FastAPI DI).

    Returns:
        ``{"status": "accepted", "job_id": <id>}`` with HTTP 202, or
        RFC 7807 404 if the job does not exist.
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

    # run_synthesis_job enqueues a Huey task synchronously (blocking call that
    # pushes a message onto the task queue).  FastAPI runs sync route handlers
    # in a threadpool, so this blocking enqueue does not stall the event loop.
    run_synthesis_job(job_id)
    _logger.info("Enqueued synthesis job %d.", job_id)
    return JSONResponse(
        status_code=202,
        content={"status": "accepted", "job_id": job_id},
    )


def _make_session_factory(session: Session) -> SessionFactory:
    """Build a :data:`SessionFactory` from an existing SQLModel Session.

    Extracts the bound engine from the session and returns a zero-argument
    callable that opens a new :class:`sqlmodel.Session` context manager.
    This is used by the SSE generator which must open its own sessions
    after the request session has been closed.

    Args:
        session: An open SQLModel ``Session`` bound to an engine.

    Returns:
        A zero-argument callable returning an
        :class:`~contextlib.AbstractContextManager` over a fresh
        :class:`sqlmodel.Session`.

    Raises:
        TypeError: If the session is not bound to a SQLAlchemy Engine.
    """
    bind = session.get_bind()
    if not isinstance(bind, Engine):
        raise TypeError(f"Session must be bound to a SQLAlchemy Engine, got {type(bind)}")

    @contextlib.contextmanager
    def _factory() -> Generator[Session]:
        with Session(bind) as s:
            yield s

    return _factory


@router.get("/{job_id}/stream", response_model=None)
async def stream_job(
    job_id: int,
    session: Annotated[Session, Depends(get_db_session)],
) -> EventSourceResponse | JSONResponse:
    """Stream real-time progress for a synthesis job via Server-Sent Events.

    Polls the database for status changes and yields SSE events:
      - ``progress``: Training in progress with ``percent`` field.
      - ``complete``: Job finished successfully.
      - ``error``: Job failed; sanitized error detail included.

    Args:
        job_id: The integer primary key of the job to stream.
        session: Database session (injected by FastAPI DI).

    Returns:
        :class:`sse_starlette.sse.EventSourceResponse` streaming events, or
        RFC 7807 404 :class:`fastapi.responses.JSONResponse` if not found.
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

    # Build a session factory so the SSE generator can open its own sessions
    # (the injected ``session`` will be closed once the route function returns).
    _factory = _make_session_factory(session)

    async def _stream() -> AsyncGenerator[dict[str, Any]]:
        async for event in job_event_stream(
            job_id=job_id,
            session_factory=_factory,
            poll_interval=_SSE_POLL_INTERVAL,
        ):
            yield event

    return EventSourceResponse(_stream())
