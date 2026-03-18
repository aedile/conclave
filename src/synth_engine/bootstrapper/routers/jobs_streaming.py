"""FastAPI router for job streaming and download endpoints.

Implements:
    - ``GET /jobs/{id}/stream``: Server-Sent Events progress stream.
    - ``GET /jobs/{id}/download``: streams the synthetic Parquet artifact.

Split from ``jobs.py`` in P26-T26.1 to separate streaming concerns from
CRUD lifecycle routes.

All 404 and error responses use RFC 7807 Problem Details format via
:func:`synth_engine.bootstrapper.errors.problem_detail`.

Task: P23-T23.2 — /jobs/{id}/download Endpoint
Task: P26-T26.1 — Split Oversized Files (Refactor Only)
"""

from __future__ import annotations

import contextlib
import hashlib
import hmac
import logging
import os
import re
from collections.abc import AsyncGenerator, Generator, Iterator
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy import Engine
from sqlmodel import Session
from sse_starlette.sse import EventSourceResponse

from synth_engine.bootstrapper.dependencies.db import get_db_session
from synth_engine.bootstrapper.errors import problem_detail
from synth_engine.bootstrapper.sse import job_event_stream
from synth_engine.modules.synthesizer.job_models import SynthesisJob
from synth_engine.shared.db import SessionFactory

_logger = logging.getLogger(__name__)

router = APIRouter(prefix="/jobs", tags=["jobs"])

#: SSE polling interval injected for testing (seconds).
_SSE_POLL_INTERVAL: float = 1.0

#: Chunk size for streaming Parquet downloads (64 KiB).
_DOWNLOAD_CHUNK_SIZE: int = 65536

#: Environment variable name for the artifact HMAC signing key.
_ARTIFACT_SIGNING_KEY_ENV: str = "ARTIFACT_SIGNING_KEY"

#: Pattern for safe filename characters in Content-Disposition header.
_SAFE_FILENAME_RE: re.Pattern[str] = re.compile(r"[^a-zA-Z0-9_\-]")


def _iter_file_chunks(path: str, chunk_size: int = _DOWNLOAD_CHUNK_SIZE) -> Iterator[bytes]:
    """Yield raw bytes from a file in fixed-size chunks.

    Reads the file at ``path`` in ``chunk_size``-byte increments without
    loading the entire content into memory (security mandate C&C 3:
    streaming download, never load whole Parquet into memory).

    Args:
        path: Absolute filesystem path to the file to read.
        chunk_size: Number of bytes per chunk.  Defaults to 64 KiB.

    Yields:
        bytes: Successive byte chunks of at most ``chunk_size`` bytes.

    Raises:
        OSError: If the file cannot be opened or read.
    """  # noqa: DOC502
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(chunk_size)
            if not chunk:
                break
            yield chunk


def _verify_artifact_signature(output_path: str) -> bool | None:
    """Check the HMAC-SHA256 signature of a Parquet artifact.

    Reads ``ARTIFACT_SIGNING_KEY`` from the environment.  If absent,
    empty, or whitespace-only, verification is skipped and ``None`` is
    returned (unsigned artifacts are acceptable in development).

    If the key is present, the ``.sig`` sidecar file at
    ``output_path + '.sig'`` is read.  If the sidecar is absent, returns
    ``False``.  The HMAC is computed incrementally by reading the artifact
    in :data:`_DOWNLOAD_CHUNK_SIZE` chunks — the entire file is never
    loaded into memory.  On a valid match, returns ``True``; on mismatch,
    returns ``False``.  If any ``OSError`` is raised while reading the
    artifact or sidecar, ``None`` is returned so the caller can
    distinguish an I/O failure from a confirmed signature mismatch.

    Args:
        output_path: Absolute filesystem path to the Parquet file.

    Returns:
        ``True`` if verification succeeds.
        ``False`` if verification fails (missing sidecar or wrong digest).
        ``None`` if signing is not enabled (no key set, whitespace-only
        key, or key that is not valid hexadecimal — logged at WARNING;
        verification skipped) or if an ``OSError`` occurred reading the
        artifact or sidecar (also logged at WARNING; verification skipped).
    """
    raw_key_env = os.environ.get(_ARTIFACT_SIGNING_KEY_ENV)
    if not raw_key_env or not raw_key_env.strip():
        return None  # signing not enabled — skip verification

    try:
        signing_key = bytes.fromhex(raw_key_env.strip())
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
        # Compute HMAC incrementally — never load the whole Parquet into memory.
        h = hmac.new(signing_key, digestmod=hashlib.sha256)
        for chunk in _iter_file_chunks(output_path, _DOWNLOAD_CHUNK_SIZE):
            h.update(chunk)
        actual_digest = h.digest()
    except OSError as exc:
        _logger.warning(
            "Failed to read artifact or sidecar for verification: %s — %s",
            exc.__class__.__name__,
            Path(output_path).name,
        )
        return None  # I/O failure — distinct from confirmed signature mismatch

    return hmac.compare_digest(actual_digest, stored_digest)


def _sanitize_filename(name: str) -> str:
    """Strip characters unsafe for use in a Content-Disposition filename.

    Removes any character that is not alphanumeric, underscore, or hyphen.
    This is a defense-in-depth measure; the primary guard is the
    ``table_name`` pattern validator on :class:`JobCreateRequest`.

    Args:
        name: Raw filename string (without extension).

    Returns:
        Sanitized string containing only ``[a-zA-Z0-9_-]`` characters.
    """
    return _SAFE_FILENAME_RE.sub("", name)


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
        - **409** if the artifact HMAC signature verification fails
          (confirmed mismatch or missing sidecar).
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
    # Returns False  → confirmed mismatch (409 Conflict).
    # Returns None   → signing disabled or I/O error (skip; proceed to stream).
    # Returns True   → verified OK.
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

    # Defense-in-depth: sanitize table_name before embedding in header.
    # The primary guard is the pattern validator on JobCreateRequest; this
    # strips any residual unsafe characters from legacy or directly-inserted rows.
    safe_name = _sanitize_filename(job.table_name)
    filename = f"{safe_name}-synthetic.parquet"
    return StreamingResponse(
        _iter_file_chunks(job.output_path),
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
