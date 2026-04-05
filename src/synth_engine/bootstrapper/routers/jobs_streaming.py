"""FastAPI router for job streaming and download endpoints.

Implements:
    - ``GET /jobs/{id}/stream``: Server-Sent Events progress stream.
    - ``GET /jobs/{id}/download``: streams the synthetic Parquet artifact.

Split from ``jobs.py`` in P26-T26.1 to separate streaming concerns from
CRUD lifecycle routes.

Authorization (T39.2, P79):
    Both endpoints filter by ``org_id`` from the verified JWT claim
    (via :func:`~synth_engine.bootstrapper.dependencies.tenant.get_current_user`).
    Accessing a resource owned by a different organization returns 404 Not Found
    (not 403 Forbidden) to prevent resource enumeration.

    ``org_id`` is derived exclusively from the verified JWT — HTTP headers
    (e.g., ``X-Org-ID``) are intentionally ignored (ATTACK-02 mitigation).

All 404 and error responses use RFC 7807 Problem Details format via
:func:`synth_engine.bootstrapper.errors.problem_detail`.

Signature verification (T42.1):
    :func:`_verify_artifact_signature` supports two formats:

    - **Versioned** (36 bytes): Built from ``ARTIFACT_SIGNING_KEYS`` multi-key
      map + ``ARTIFACT_SIGNING_KEY_ACTIVE``.  Key ID is extracted from the
      signature prefix and matched in the key map.

    - **Legacy** (32 bytes): Built from ``ARTIFACT_SIGNING_KEY`` single-key
      setting.  The legacy key is resolved from the key map returned by
      :func:`~synth_engine.shared.security.hmac_signing.build_key_map_from_settings`
      and verified via :func:`hmac.compare_digest`.

    Both formats use an incremental HMAC computation via :mod:`hmac` —
    the file is read in fixed-size chunks and fed to ``hmac.new().update()``
    so the entire artifact is never loaded into memory (security mandate C&C 3).

Task: P23-T23.2 — /jobs/{id}/download Endpoint
Task: P26-T26.1 — Split Oversized Files (Refactor Only)
Task: T39.2 — Add Authorization & IDOR Protection on All Resource Endpoints
Task: T42.1 — Artifact Signing Key Versioning
Task: P79-T79.2 — Migrate routers to TenantContext (org_id filtering)
"""

from __future__ import annotations

import contextlib
import hashlib
import hmac as hmac_mod
import logging
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
from synth_engine.bootstrapper.dependencies.tenant import TenantContext, get_current_user
from synth_engine.bootstrapper.errors import problem_detail
from synth_engine.bootstrapper.sse import job_event_stream
from synth_engine.modules.synthesizer.jobs.job_models import SynthesisJob
from synth_engine.shared.db import SessionFactory
from synth_engine.shared.security.hmac_signing import (
    HMAC_DIGEST_SIZE,
    KEY_ID_SIZE,
    LEGACY_KEY_ID,
    VERSIONED_SIGNATURE_SIZE,
    build_key_map_from_settings,
)

_logger = logging.getLogger(__name__)

router = APIRouter(prefix="/jobs", tags=["jobs"])

#: SSE polling interval injected for testing (seconds).
_SSE_POLL_INTERVAL: float = 1.0

#: Chunk size for streaming Parquet downloads (64 KiB).
_DOWNLOAD_CHUNK_SIZE: int = 65536

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

    Supports both versioned (36-byte ``KEY_ID || HMAC``) and legacy
    (32-byte bare HMAC) sidecar formats.

    Key resolution order:
      1. Multi-key map from ``ARTIFACT_SIGNING_KEYS`` (versioned mode).
      2. Single key from ``ARTIFACT_SIGNING_KEY`` mapped to
         :data:`LEGACY_KEY_ID` (legacy mode).
      3. If neither is configured, returns ``None`` (skip verification).

    If the key is present, the ``.sig`` sidecar file at
    ``output_path + '.sig'`` is read.  If the sidecar is absent, returns
    ``False``.  HMAC is computed incrementally in :data:`_DOWNLOAD_CHUNK_SIZE`
    chunks so the entire artifact is never loaded into memory
    (security mandate C&C 3).

    Args:
        output_path: Absolute filesystem path to the Parquet file.

    Returns:
        ``True`` if verification succeeds.
        ``False`` if verification fails (missing sidecar or wrong digest).
        ``None`` if signing is not enabled or an ``OSError`` occurred.
    """
    key_map = build_key_map_from_settings()
    if key_map is None:
        return None  # signing not enabled — skip verification

    sig_path = output_path + ".sig"
    if not Path(sig_path).exists():
        _logger.warning(
            "Signature sidecar not found for artifact %s; rejecting download.",
            Path(output_path).name,
        )
        return False

    try:
        stored_signature = Path(sig_path).read_bytes()
        # Compute HMAC incrementally — never load whole Parquet into memory.
        h = hmac_mod.new(b"placeholder", digestmod=hashlib.sha256)
        # Resolve the key from stored_signature before computing HMAC.
        if len(stored_signature) == VERSIONED_SIGNATURE_SIZE:
            key_id = stored_signature[:KEY_ID_SIZE]
            stored_digest = stored_signature[KEY_ID_SIZE:]
        elif len(stored_signature) == HMAC_DIGEST_SIZE:
            key_id = LEGACY_KEY_ID
            stored_digest = stored_signature
        else:
            return False

        key = key_map.get(key_id)
        if key is None:
            return False

        h = hmac_mod.new(key, digestmod=hashlib.sha256)
        for chunk in _iter_file_chunks(output_path, _DOWNLOAD_CHUNK_SIZE):
            h.update(chunk)
        computed = h.digest()
    except OSError as exc:
        _logger.warning(
            "Failed to read artifact or sidecar for verification: %s — %s",
            type(exc).__name__,
            Path(output_path).name,
        )
        return None  # I/O failure — distinct from confirmed signature mismatch

    return hmac_mod.compare_digest(computed, stored_digest)


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
    current_user: Annotated[TenantContext, Depends(get_current_user)],
) -> EventSourceResponse | JSONResponse:
    """Stream real-time progress for a synthesis job via Server-Sent Events.

    Returns 404 if the job does not exist **or** belongs to a different
    organization (IDOR protection).

    Polls the database for status changes and yields SSE events:
      - ``progress``: Training in progress with ``percent`` field.
      - ``complete``: Job finished successfully.
      - ``error``: Job failed; sanitized error detail included.

    Args:
        job_id: The integer primary key of the job to stream.
        session: Database session (injected by FastAPI DI).
        current_user: Resolved tenant identity (org_id, user_id, role) from JWT.

    Returns:
        :class:`sse_starlette.sse.EventSourceResponse` streaming events, or
        RFC 7807 404 :class:`fastapi.responses.JSONResponse` if not found or
        org mismatch.
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
    current_user: Annotated[TenantContext, Depends(get_current_user)],
) -> StreamingResponse | JSONResponse:
    """Stream the synthetic Parquet artifact for a completed job.

    Returns 404 if the job does not exist **or** belongs to a different
    organization (IDOR protection).

    Verifies the artifact HMAC-SHA256 signature before serving (if
    any signing key is configured).  Supports both versioned
    (``KEY_ID || HMAC``) and legacy (bare 32-byte HMAC) sidecar formats.
    Uses :class:`~fastapi.responses.StreamingResponse` to stream raw bytes
    in 64 KiB chunks — the entire file is never loaded into memory
    (security mandate C&C 3).

    Args:
        job_id: The integer primary key of the job.
        session: Database session (injected by FastAPI DI).
        current_user: Resolved tenant identity (org_id, user_id, role) from JWT.

    Returns:
        :class:`~fastapi.responses.StreamingResponse` with
        ``Content-Type: application/octet-stream`` and
        ``Content-Disposition: attachment; filename="<table_name>-synthetic.parquet"``
        on success; or RFC 7807 JSON responses for error conditions:

        - **404** if the job does not exist or org mismatch.
        - **404** if the job status is not ``COMPLETE``.
        - **404** if ``output_path`` is ``None`` or the file does not exist.
        - **409** if the artifact HMAC signature verification fails
          (confirmed mismatch or missing sidecar).
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
