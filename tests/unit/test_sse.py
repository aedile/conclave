"""Unit tests for bootstrapper/sse.py — async SSE generator and _poll_job helper.

Covers DevOps finding D2: sync DB read blocking event loop in async SSE generator.

Task: P5-T5.1 — Task Orchestration API Core (DevOps fix)
"""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_job(
    *,
    job_id: int = 1,
    status: str = "TRAINING",
    current_epoch: int = 3,
    total_epochs: int = 10,
    error_msg: str | None = None,
) -> Any:
    """Return a lightweight mock that satisfies the SynthesisJob field contract."""
    job = MagicMock()
    job.id = job_id
    job.status = status
    job.current_epoch = current_epoch
    job.total_epochs = total_epochs
    job.error_msg = error_msg
    return job


def _session_factory_for(job: Any | None) -> Any:
    """Return a SessionFactory stub whose session.get() returns ``job``."""
    session = MagicMock()
    session.get.return_value = job

    @contextmanager  # type: ignore[misc]
    def _factory() -> Any:
        yield session

    return _factory


# ---------------------------------------------------------------------------
# _poll_job — synchronous helper
# ---------------------------------------------------------------------------


class TestPollJob:
    """Tests for the synchronous _poll_job helper."""

    def test_poll_job_returns_job_when_found(self) -> None:
        """_poll_job must return the job returned by session.get."""
        from synth_engine.bootstrapper.sse import _poll_job

        job = _make_job()
        factory = _session_factory_for(job)

        result = _poll_job(factory, 1)  # type: ignore[arg-type]
        assert result is job

    def test_poll_job_returns_none_when_not_found(self) -> None:
        """_poll_job must return None when session.get returns None."""
        from synth_engine.bootstrapper.sse import _poll_job

        factory = _session_factory_for(None)
        result = _poll_job(factory, 99)  # type: ignore[arg-type]
        assert result is None


# ---------------------------------------------------------------------------
# job_event_stream — async generator
# ---------------------------------------------------------------------------


class TestJobEventStream:
    """Tests for the job_event_stream async generator."""

    @pytest.mark.asyncio
    async def test_stream_yields_complete_event_for_complete_job(self) -> None:
        """Stream must yield a 'complete' event and stop for a COMPLETE job."""
        from synth_engine.bootstrapper.sse import job_event_stream

        job = _make_job(status="COMPLETE", current_epoch=10, total_epochs=10)
        factory = _session_factory_for(job)

        events = []
        async for event in job_event_stream(
            job_id=1,
            session_factory=factory,  # type: ignore[arg-type]
            poll_interval=0.0,
            max_cycles=5,
        ):
            events.append(event)

        assert len(events) == 1
        assert events[0]["event"] == "complete"

    @pytest.mark.asyncio
    async def test_stream_yields_error_event_for_failed_job(self) -> None:
        """Stream must yield an 'error' event and stop for a FAILED job."""
        from synth_engine.bootstrapper.sse import job_event_stream

        job = _make_job(status="FAILED", error_msg="OOM")
        factory = _session_factory_for(job)

        events = []
        async for event in job_event_stream(
            job_id=1,
            session_factory=factory,  # type: ignore[arg-type]
            poll_interval=0.0,
            max_cycles=5,
        ):
            events.append(event)

        assert len(events) == 1
        assert events[0]["event"] == "error"

    @pytest.mark.asyncio
    async def test_stream_yields_progress_event_for_training_job(self) -> None:
        """Stream must yield a 'progress' event for a TRAINING job."""
        from synth_engine.bootstrapper.sse import job_event_stream

        # First call TRAINING, second call COMPLETE — stream emits progress then complete.
        job_training = _make_job(status="TRAINING", current_epoch=1, total_epochs=10)
        job_complete = _make_job(status="COMPLETE", current_epoch=10, total_epochs=10)

        call_count = 0

        @contextmanager  # type: ignore[misc]
        def _cycling_factory() -> Any:
            nonlocal call_count
            session = MagicMock()
            session.get.return_value = job_training if call_count == 0 else job_complete
            call_count += 1
            yield session

        events = []
        async for event in job_event_stream(
            job_id=1,
            session_factory=_cycling_factory,  # type: ignore[arg-type]
            poll_interval=0.0,
            max_cycles=5,
        ):
            events.append(event)

        event_types = [e["event"] for e in events]
        assert "progress" in event_types
        assert event_types[-1] == "complete"

    @pytest.mark.asyncio
    async def test_stream_stops_when_job_not_found(self) -> None:
        """Stream must stop immediately when the job does not exist."""
        from synth_engine.bootstrapper.sse import job_event_stream

        factory = _session_factory_for(None)

        events = []
        async for event in job_event_stream(
            job_id=99,
            session_factory=factory,  # type: ignore[arg-type]
            poll_interval=0.0,
            max_cycles=5,
        ):
            events.append(event)

        assert events == []

    @pytest.mark.asyncio
    async def test_stream_db_read_dispatched_to_thread(self) -> None:
        """asyncio.to_thread must be called for every DB poll cycle.

        This verifies that the synchronous DB read is never run on the event
        loop thread (DevOps finding D2).
        """
        import synth_engine.bootstrapper.sse as sse_module
        from synth_engine.bootstrapper.sse import _poll_job, job_event_stream

        job = _make_job(status="COMPLETE", current_epoch=10, total_epochs=10)
        factory = _session_factory_for(job)

        to_thread_calls: list[Any] = []
        original_to_thread = asyncio.to_thread

        async def _spy_to_thread(func: Any, *args: Any, **kwargs: Any) -> Any:
            to_thread_calls.append(func)
            return await original_to_thread(func, *args, **kwargs)

        with patch.object(sse_module, "asyncio") as mock_asyncio:
            # Allow asyncio.sleep to pass through as a no-op coroutine
            mock_asyncio.sleep = AsyncMock(return_value=None)

            # Spy on to_thread — run real _poll_job in thread
            async def _real_to_thread(func: Any, *args: Any, **kwargs: Any) -> Any:
                to_thread_calls.append(func)
                return await original_to_thread(func, *args, **kwargs)

            mock_asyncio.to_thread = _real_to_thread

            async for _ in job_event_stream(
                job_id=1,
                session_factory=factory,  # type: ignore[arg-type]
                poll_interval=0.0,
                max_cycles=5,
            ):
                pass

        assert len(to_thread_calls) >= 1, "asyncio.to_thread was never called"
        assert all(fn is _poll_job for fn in to_thread_calls)
