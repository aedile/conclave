"""FastAPI router for Jobs endpoints.

Implements CRUD for :class:`SynthesisJob` resources plus:
    - ``POST /jobs/{id}/start``: enqueues the Huey synthesis task.
    - ``GET /jobs/{id}/stream``: Server-Sent Events progress stream.
    - ``POST /jobs/{id}/shred``: NIST 800-88 compliant artifact erasure.
    - ``GET /jobs/{id}/download``: streams the synthetic Parquet artifact.

Cursor-based pagination uses the integer ``id`` column as the cursor.
Pattern: ``GET /jobs?after=<cursor>&limit=20`` returns jobs where
``id > cursor``, ordered by ``id`` ascending.

All 404 and error responses use RFC 7807 Problem Details format via
:func:`synth_engine.bootstrapper.errors.problem_detail`.

Task: P5-T5.1 — Task Orchestration API Core
Task: P22-T22.1 — Job Schema DP Parameters
Task: P23-T23.2 — /jobs/{id}/download Endpoint
Task: P23-T23.4 — Cryptographic Erasure Endpoint
"""

from __future__ import annotations

import contextlib
import logging
import os
from collections.abc import AsyncGenerator, Generator, Iterator
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse, StreamingResponse
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
from synth_engine.modules.synthesizer.shred import shred_artifacts
from synth_engine.modules.synthesizer.tasks import run_synthesis_job
from synth_engine.shared.db import SessionFactory
from synth_engine.shared.security.audit import get_audit_logger
from synth_engine.shared.security.hmac_signing import verify_hmac

_logger = logging.getLogger(__name__)

router = APIRouter(prefix="/jobs", tags=["jobs"])

#: Default page size for cursor-based pagination.
_DEFAULT_PAGE_SIZE: int = 20

#: Maximum page size a caller may request.
_MAX_PAGE_SIZE: int = 100

#: SSE polling interval injected for testing (seconds).
_SSE_POLL_INTERVAL: float = 1.0

#: Job status that permits the shred operation.
_SHRED_ELIGIBLE_STATUS: str = "COMPLETE"

#: Job status applied after successful artifact erasure.
_SHREDDED_STATUS: str = "SHREDDED"

#: Chunk size for streaming Parquet downloads (64 KiB).
_DOWNLOAD_CHUNK_SIZE: int = 65536

#: Environment variable name for the artifact HMAC signing key.
_ARTIFACT_SIGNING_KEY_ENV: str = "ARTIFACT_SIGNING_KEY"


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


@router.post("/{job_id}/shred", status_code=200)
def shred_job(
    job_id: int,
    session: Annotated[Session, Depends(get_db_session)],
) -> JSONResponse:
    """Shred all synthesis artifacts for a COMPLETE job (NIST SP 800-88).

    Deletes the generated Parquet output, its HMAC-SHA256 signature sidecar,
    and the trained model artifact pickle from the filesystem.  Emits a WORM
    audit event and transitions the job status to ``SHREDDED``.

    Only jobs in ``COMPLETE`` status are eligible.  Jobs in any other status
    (including already-``SHREDDED`` jobs) return a 404 Problem Detail response.

    Args:
        job_id: The integer primary key of the job to shred.
        session: Database session (injected by FastAPI DI).

    Returns:
        ``{"status": "SHREDDED", "job_id": <id>}`` with HTTP 200 on success,
        or RFC 7807 404 if the job does not exist or is not eligible.
    """
    job = session.get(SynthesisJob, job_id)

    if job is None or job.status != _SHRED_ELIGIBLE_STATUS:
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
    shred_artifacts(job)

    # Emit WORM audit event (CONSTITUTION Priority 0: Security).
    # Must be called AFTER deletion so the event records what was accomplished.
    try:
        audit = get_audit_logger()
        audit.log_event(
            event_type="ARTIFACT_SHREDDED",
            actor="system/api",
            resource=f"synthesis_job/{job_id}",
            action="shred",
            details={
                "job_id": str(job_id),
                "table_name": job.table_name,
            },
        )
    except Exception:
        # Audit log failure must NOT prevent the status transition —
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


def _iter_file_chunks(path: str, chunk_size: int = _DOWNLOAD_CHUNK_SIZE) -> Iterator[bytes]:
    """Yield raw bytes from a file in fixed-size chunks.

    Reads the file at ``path`` in ``chunk_size``-byte increments without
    loading the entire content into memory (security mandate C&C 3:
    streaming download, never load whole Parquet into memory).

    Args:
        path: Absolute filesystem path to the file to read.
        chunk_size: Number of bytes per chunk.  Defaults to 64 KiB.

    Yields:
        Successive byte chunks of at most ``chunk_size`` bytes.

    Raises:
        OSError: If the file cannot be opened or read.
    """
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(chunk_size)
            if not chunk:
                break
            yield chunk


def _verify_artifact_signature(output_path: str) -> bool | None:
    """Check the HMAC-SHA256 signature of a Parquet artifact.

    Reads ``ARTIFACT_SIGNING_KEY`` from the environment.  If absent or
    empty, verification is skipped and ``None`` is returned (unsigned
    artifacts are acceptable in development).

    If the key is present, reads the ``.sig`` sidecar file at
    ``output_path + '.sig'``.  If the sidecar is absent or the digest
    does not match, returns ``False``.  On a valid match, returns
    ``True``.

    Args:
        output_path: Absolute filesystem path to the Parquet file.

    Returns:
        ``True`` if verification succeeds.
        ``False`` if verification fails (missing sidecar or wrong digest).
        ``None`` if signing is not enabled (no key set).
    """
    signing_key_hex = os.environ.get(_ARTIFACT_SIGNING_KEY_ENV)
    if not signing_key_hex:
        return None  # signing not enabled — skip verification

    try:
        signing_key = bytes.fromhex(signing_key_hex)
    except ValueError:
        _logger.warning(
            "ARTIFACT_SIGNING_KEY is not valid hex; skipping signature verification for %s",
            Path(output_path).name,
        )
        return None

    if len(signing_key) == 0:
        _logger.warning(
            "ARTIFACT_SIGNING_KEY decoded to empty bytes; skipping signature verification."
        )
        return None

    sig_path = output_path + ".sig"
    if not Path(sig_path).exists():
        _logger.warning(
            "Signature sidecar not found for artifact %s; rejecting download.",
            Path(output_path).name,
        )
        return False

    try:
        stored_digest = Path(sig_path).read_bytes()
        parquet_bytes = Path(output_path).read_bytes()
    except OSError as exc:
        _logger.warning(
            "Failed to read artifact or sidecar for verification: %s",
            str(exc),
        )
        return False

    return verify_hmac(signing_key, parquet_bytes, stored_digest)


@router.get("/{job_id}/download", response_model=None)
def download_job(
    job_id: int,
    session: Annotated[Session, Depends(get_db_session)],
) -> StreamingResponse | JSONResponse:
    """Stream the synthetic Parquet artifact for a completed job.

    Verifies the artifact HMAC-SHA256 signature before serving (if
    ``ARTIFACT_SIGNING_KEY`` is set in the environment).  Uses
    :class:`~fastapi.responses.StreamingResponse` to stream raw bytes
    in 64 KiB chunks — the entire file is never loaded into memory
    (security mandate C&C 3).

    Args:
        job_id: The integer primary key of the job.
        session: Database session (injected by FastAPI DI).

    Returns:
        :class:`~fastapi.responses.StreamingResponse` with
        ``Content-Type: application/octet-stream`` and
        ``Content-Disposition: attachment; filename="<table_name>-synthetic.parquet"``
        on success; or RFC 7807 JSON responses for error conditions:

        - **404** if the job does not exist.
        - **404** if the job status is not ``COMPLETE``.
        - **404** if ``output_path`` is ``None`` or the file does not exist.
        - **409** if the artifact HMAC signature verification fails.
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

    if job.status != "COMPLETE":
        return JSONResponse(
            status_code=404,
            content=problem_detail(
                status=404,
                title="Not Found",
                detail=f"SynthesisJob {job_id} is not complete (status={job.status}).",
            ),
        )

    if job.output_path is None:
        return JSONResponse(
            status_code=404,
            content=problem_detail(
                status=404,
                title="Not Found",
                detail=f"SynthesisJob {job_id} has no output artifact.",
            ),
        )

    if not Path(job.output_path).exists():
        _logger.warning(
            "Artifact file not found for job %d: %s",
            job_id,
            Path(job.output_path).name,
        )
        return JSONResponse(
            status_code=404,
            content=problem_detail(
                status=404,
                title="Not Found",
                detail=f"Artifact for SynthesisJob {job_id} is not available.",
            ),
        )

    # Verify HMAC signature before serving (C&C 2 / AC2).
    verification_result = _verify_artifact_signature(job.output_path)
    if verification_result is False:
        _logger.warning(
            "Artifact signature verification failed for job %d; rejecting download.",
            job_id,
        )
        return JSONResponse(
            status_code=409,
            content=problem_detail(
                status=409,
                title="Conflict",
                detail="Artifact signature verification failed — file may have been tampered with.",
            ),
        )

    filename = f"{job.table_name}-synthetic.parquet"
    return StreamingResponse(
        _iter_file_chunks(job.output_path),
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
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
        :class:`~sqlmodel.Session`.

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
