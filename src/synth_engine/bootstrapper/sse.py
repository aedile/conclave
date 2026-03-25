"""Server-Sent Events (SSE) streaming utilities for the Conclave Engine.

Provides :func:`job_event_stream` — an async generator that polls the
database for :class:`SynthesisJob` status changes and yields SSE events
representing real-time training progress to the frontend operator UI.

Event types emitted:
    - ``progress``: Training in progress.  JSON data includes
      ``status``, ``current_epoch``, ``total_epochs``, and ``percent``.
    - ``complete``: Job reached ``COMPLETE`` status.
    - ``error``: Job reached ``FAILED`` status.  ``detail`` is sanitized
      via :func:`~synth_engine.shared.errors.safe_error_msg` (ADV-036+044).

Design notes:
    - SSE is used instead of WebSockets to avoid enterprise firewall
      interference with WebSocket upgrades (per backlog Context & Constraints).
    - Polling interval is 1 second — low enough for responsive UX, high
      enough to avoid overwhelming SQLite/PostgreSQL in development.
    - The stream terminates when the job reaches a terminal state
      (``COMPLETE`` or ``FAILED``) or after a configurable timeout.
    - The DB read is dispatched to a thread pool via ``asyncio.to_thread``
      so the synchronous SQLModel session never blocks the event loop
      (DevOps finding D2 — P5-T5.1 review).

Task: P5-T5.1 — Task Orchestration API Core
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING, Any

from synth_engine.shared.db import SessionFactory
from synth_engine.shared.errors import safe_error_msg

if TYPE_CHECKING:
    from synth_engine.modules.synthesizer.jobs.job_models import SynthesisJob

_logger = logging.getLogger(__name__)

#: Polling interval between database reads (seconds).
_POLL_INTERVAL_S: float = 1.0

#: Maximum number of poll cycles before the stream times out.
#: At 1 s/poll, 3600 = 1 hour max stream lifetime.
_MAX_POLL_CYCLES: int = 3600

#: Terminal job statuses — stream ends when one of these is reached.
_TERMINAL_STATUSES: frozenset[str] = frozenset({"COMPLETE", "FAILED"})


def _build_progress_data(
    status: str,
    current_epoch: int,
    total_epochs: int,
) -> dict[str, Any]:
    """Build the SSE data dict for a ``progress`` event.

    Args:
        status: Current job status string (e.g. ``"TRAINING"``).
        current_epoch: Most recently completed training epoch.
        total_epochs: Total epochs requested.

    Returns:
        Dict with ``status``, ``current_epoch``, ``total_epochs``,
        and ``percent`` (0-100 integer).
    """
    percent = int(current_epoch / total_epochs * 100) if total_epochs > 0 else 0
    return {
        "status": status,
        "current_epoch": current_epoch,
        "total_epochs": total_epochs,
        "percent": percent,
    }


def _poll_job(
    session_factory: SessionFactory,
    job_id: int,
) -> SynthesisJob | None:
    """Read a single :class:`SynthesisJob` row from the database synchronously.

    This function is designed to be called via :func:`asyncio.to_thread` so
    that the blocking SQLModel session never occupies the event loop thread.
    The full session lifecycle (open → query → close) is contained here so
    no session object crosses thread boundaries.

    Args:
        session_factory: Zero-argument callable returning a
            :class:`sqlmodel.Session` context manager.
        job_id: Primary key of the job to fetch.

    Returns:
        The :class:`SynthesisJob` instance, or ``None`` if not found.
    """
    # Deferred import: avoids pulling synthesizer module-level state into
    # every bootstrapper import at startup.
    from synth_engine.modules.synthesizer.jobs.job_models import SynthesisJob

    with session_factory() as session:
        return session.get(SynthesisJob, job_id)


async def job_event_stream(
    job_id: int,
    session_factory: SessionFactory,
    poll_interval: float = _POLL_INTERVAL_S,
    max_cycles: int = _MAX_POLL_CYCLES,
) -> AsyncGenerator[dict[str, Any]]:
    """Async generator that streams SSE events for a synthesis job.

    Polls the database every ``poll_interval`` seconds and yields SSE event
    dicts for consumption by ``sse-starlette``'s ``EventSourceResponse``.

    The synchronous DB read is dispatched to a worker thread via
    ``asyncio.to_thread`` on every cycle so the event loop is never blocked
    (DevOps finding D2).

    Yields event dicts of the form::

        {"event": "progress", "data": '{"status": "TRAINING", ...}'}
        {"event": "complete", "data": '{}'}
        {"event": "error",    "data": '{"detail": "<sanitized msg>"}'}

    Args:
        job_id: Primary key of the ``SynthesisJob`` to stream.
        session_factory: Zero-argument callable returning a
            :class:`sqlmodel.Session` context manager.  Used to poll the DB.
        poll_interval: Seconds between database polls.  Default: 1.0.
        max_cycles: Maximum number of poll cycles before timeout.  Default: 3600.

    Yields:
        AsyncGenerator[dict[str, Any]]: SSE event dicts compatible with ``sse-starlette``'s
            ``EventSourceResponse``.
    """
    for _ in range(max_cycles):
        job = await asyncio.to_thread(_poll_job, session_factory, job_id)

        if job is None:
            _logger.warning("SSE stream: job %d not found; closing stream.", job_id)
            return

        if job.status == "COMPLETE":
            data = _build_progress_data(
                status=job.status,
                current_epoch=job.current_epoch,
                total_epochs=job.total_epochs,
            )
            yield {"event": "complete", "data": json.dumps(data)}
            return

        if job.status in _TERMINAL_STATUSES:
            # Only FAILED reaches here — COMPLETE was handled above.
            # Using _TERMINAL_STATUSES membership check (not inline string literal)
            # ensures this branch stays in sync if terminal states are ever extended.
            error_detail = safe_error_msg(job.error_msg or "Unknown error")
            yield {"event": "error", "data": json.dumps({"detail": error_detail})}
            return

        # Emit progress event for QUEUED or TRAINING states
        data = _build_progress_data(
            status=job.status,
            current_epoch=job.current_epoch,
            total_epochs=job.total_epochs,
        )
        yield {"event": "progress", "data": json.dumps(data)}

        await asyncio.sleep(poll_interval)

    # Timeout: emit a timeout error event
    _logger.warning("SSE stream: job %d timed out after %d cycles.", job_id, max_cycles)
    yield {
        "event": "error",
        "data": json.dumps({"detail": "Stream timed out waiting for job completion."}),
    }
