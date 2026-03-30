"""Integration tests for SSE progress streaming (T5.1).

Backlog Testing & Quality Gates (verbatim):
  - Write an integration test that creates a mock 10-second Huey job,
    connects to the SSE endpoint, and verifies that it receives sequential
    `progress` events (10%, 20%, etc.) until `complete`.
  - Verify that any unhandled exception in an endpoint yields a valid
    RFC 7807 JSON response (with `type`, `title`, `status`, and `detail` fields).

These are INTEGRATION tests per the two-gate policy (Rule 3).  They use
real in-process SSE streaming via the sse-starlette library and an
in-memory SQLite database (acceptable for SSE streaming tests — no
PostgreSQL-specific features tested here).

Task: P5-T5.1 — Task Orchestration API Core
CONSTITUTION Priority 3: TDD — RED phase
"""

from __future__ import annotations

import json
import threading
import time
from typing import Any
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

pytestmark = pytest.mark.integration


def _make_integration_app(engine: Any) -> Any:
    """Build a fully-wired FastAPI app for integration tests.

    Args:
        engine: SQLAlchemy engine with test database tables created.

    Returns:
        A FastAPI app with all routers and error handlers wired.
    """
    from synth_engine.bootstrapper.dependencies.db import get_db_session
    from synth_engine.bootstrapper.main import create_app
    from synth_engine.bootstrapper.routers.jobs import router as jobs_router

    app = create_app()
    app.include_router(jobs_router)

    def _override() -> Any:
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_db_session] = _override
    return app


class TestSSEProgressStreaming:
    """Integration test: mock Huey job with sequential SSE progress events.

    Per backlog: creates a mock 10-second Huey job, connects to the SSE
    endpoint, and verifies sequential progress events (10%, 20%, ..., 100%)
    until 'complete'.
    """

    @pytest.mark.asyncio
    async def test_sse_streams_sequential_progress_events(self) -> None:
        """SSE endpoint must yield sequential progress events until complete.

        Simulates a job progressing through epochs 1, 2, 5, 10 of 10
        (i.e., 10%, 20%, 50%, 100%) by updating the database in a background
        thread while the SSE endpoint is streaming.

        Verifies:
        - At least one 'progress' event is received.
        - A 'complete' event is received.
        - The percent values in progress events are monotonically increasing.
        - All observed percent values match the expected epoch-derived values.
          The SSE stream polls immediately on first request, so the initial
          QUEUED state (current_epoch=0) produces a 0% progress event.
          Subsequent polls may capture 10%, 20%, or 50% depending on timing.
          All of {0, 10, 20, 50} are valid observable values before COMPLETE.
        """
        from synth_engine.modules.synthesizer.jobs.job_models import SynthesisJob

        engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        SQLModel.metadata.create_all(engine)

        # Create a job in QUEUED state with 10 total epochs.
        with Session(engine) as session:
            job = SynthesisJob(
                table_name="customers",
                parquet_path="/tmp/customers.parquet",
                total_epochs=10,
                num_rows=100,
                status="QUEUED",
            )
            session.add(job)
            session.commit()
            session.refresh(job)
            job_id = job.id

        def _advance_job() -> None:
            """Background thread: simulate epoch-by-epoch job progress.

            Steps produce percent values: 10%, 20%, 50%, then COMPLETE@100%.
            """
            steps = [
                ("TRAINING", 1),  # 10%
                ("TRAINING", 2),  # 20%
                ("TRAINING", 5),  # 50%
                ("COMPLETE", 10),  # 100% — triggers terminal event
            ]
            for status, epoch in steps:
                time.sleep(0.15)
                with Session(engine) as s:
                    j = s.get(SynthesisJob, job_id)
                    if j is None:
                        return
                    j.status = status
                    j.current_epoch = epoch
                    s.add(j)
                    s.commit()

        app = _make_integration_app(engine)

        thread = threading.Thread(target=_advance_job, daemon=True)

        with (
            patch(
                "synth_engine.bootstrapper.dependencies.vault.VaultState.is_sealed",
                return_value=False,
            ),
            patch(
                "synth_engine.bootstrapper.dependencies.licensing.LicenseState.is_licensed",
                return_value=True,
            ),
        ):
            thread.start()
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get(
                    f"/api/v1/jobs/{job_id}/stream",
                    headers={"Accept": "text/event-stream"},
                )

        thread.join(timeout=5)

        assert response.status_code == 200
        content = response.text

        # Parse all SSE events from the response body.
        progress_percents: list[int] = []
        has_complete_event = False

        current_event_type: str | None = None
        for line in content.splitlines():
            if line.startswith("event:"):
                current_event_type = line[6:].strip()
            elif line.startswith("data:"):
                data_str = line[5:].strip()
                try:
                    data = json.loads(data_str)
                except json.JSONDecodeError:
                    continue

                if current_event_type == "progress" and "percent" in data:
                    progress_percents.append(int(data["percent"]))
                elif current_event_type == "complete":
                    has_complete_event = True

        # Must have received at least one progress event before complete.
        assert len(progress_percents) >= 1, (
            "SSE stream must emit at least one 'progress' event before 'complete'"
        )

        # Must end with a complete event.
        assert has_complete_event, "SSE stream must emit a 'complete' event when job finishes"

        # Percent values must be monotonically non-decreasing.
        for i in range(1, len(progress_percents)):
            assert progress_percents[i] >= progress_percents[i - 1], (
                f"Progress percents are not sequential: {progress_percents}"
            )

        # All observed progress percents must be one of the expected values.
        # 0% is included because the SSE stream polls immediately: the first poll
        # captures the QUEUED state (current_epoch=0, total_epochs=10 → 0%).
        # Subsequent polls may capture 10%, 20%, or 50% depending on timing.
        expected_possible = {0, 10, 20, 50}
        for pct in progress_percents:
            assert pct in expected_possible, (
                f"Unexpected percent value {pct}; expected one of {expected_possible}. "
                f"Full sequence: {progress_percents}"
            )

    @pytest.mark.asyncio
    async def test_sse_events_contain_percent_field(self) -> None:
        """SSE progress events must contain a 'percent' field in the JSON data."""
        from synth_engine.modules.synthesizer.jobs.job_models import SynthesisJob

        engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        SQLModel.metadata.create_all(engine)

        # Create a job already in TRAINING state at 50%
        with Session(engine) as session:
            job = SynthesisJob(
                table_name="orders",
                parquet_path="/tmp/orders.parquet",
                total_epochs=10,
                num_rows=100,
                status="TRAINING",
                current_epoch=5,
            )
            session.add(job)
            session.commit()
            session.refresh(job)
            job_id = job.id

        def _complete_job() -> None:
            time.sleep(0.2)
            with Session(engine) as s:
                j = s.get(SynthesisJob, job_id)
                if j is None:
                    return
                j.status = "COMPLETE"
                j.current_epoch = 10
                s.add(j)
                s.commit()

        app = _make_integration_app(engine)

        thread = threading.Thread(target=_complete_job, daemon=True)

        with (
            patch(
                "synth_engine.bootstrapper.dependencies.vault.VaultState.is_sealed",
                return_value=False,
            ),
            patch(
                "synth_engine.bootstrapper.dependencies.licensing.LicenseState.is_licensed",
                return_value=True,
            ),
        ):
            thread.start()
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get(
                    f"/api/v1/jobs/{job_id}/stream",
                    headers={"Accept": "text/event-stream"},
                )

        thread.join(timeout=5)

        content = response.text
        # Parse SSE data lines and look for a percent field
        has_percent = False
        for line in content.splitlines():
            if line.startswith("data:"):
                data_str = line[5:].strip()
                try:
                    data = json.loads(data_str)
                    if "percent" in data:
                        has_percent = True
                        break
                except json.JSONDecodeError:
                    continue
        assert has_percent, "No SSE event contained a 'percent' field"
        # Specific: the response status is 200 (streaming completed)
        assert response.status_code == 200, (
            f"Expected 200 status from SSE stream, got {response.status_code}"
        )


class TestRFC7807UnhandledExceptions:
    """Integration test: unhandled exceptions yield valid RFC 7807 responses.

    Per backlog: verify that any unhandled exception in an endpoint yields a
    valid RFC 7807 JSON response (with type, title, status, and detail fields).
    """

    @pytest.mark.asyncio
    async def test_unhandled_exception_yields_rfc7807(self) -> None:
        """Any unhandled endpoint exception must produce RFC 7807 Problem Details.

        This integration test exercises the full FastAPI exception handler stack
        with a real (in-process) ASGI transport, not a mock.
        """
        from synth_engine.bootstrapper.main import create_app

        app = create_app()

        @app.get("/trigger-error")
        async def _boom() -> None:
            raise RuntimeError("Unexpected internal error at /secret/path/module.py")

        with (
            patch(
                "synth_engine.bootstrapper.dependencies.vault.VaultState.is_sealed",
                return_value=False,
            ),
            patch(
                "synth_engine.bootstrapper.dependencies.licensing.LicenseState.is_licensed",
                return_value=True,
            ),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get("/trigger-error")

        assert response.status_code == 500
        body = response.json()

        # RFC 7807 required fields
        assert "type" in body, "RFC 7807: 'type' field missing"
        assert "title" in body, "RFC 7807: 'title' field missing"
        assert "status" in body, "RFC 7807: 'status' field missing"
        assert "detail" in body, "RFC 7807: 'detail' field missing"
        assert body["status"] == 500, "RFC 7807: status must match HTTP status code"

        # Path must be sanitized -- never leak internal paths
        assert "/secret/path" not in body.get("detail", ""), (
            "Internal path leaked in RFC 7807 detail field (ADV-036+044)"
        )

    @pytest.mark.asyncio
    async def test_rfc7807_response_content_type_is_json(self) -> None:
        """RFC 7807 error responses must use application/json content type."""
        from synth_engine.bootstrapper.main import create_app

        app = create_app()

        @app.get("/trigger-error-ct")
        async def _boom2() -> None:
            raise ValueError("Bad input")

        with (
            patch(
                "synth_engine.bootstrapper.dependencies.vault.VaultState.is_sealed",
                return_value=False,
            ),
            patch(
                "synth_engine.bootstrapper.dependencies.licensing.LicenseState.is_licensed",
                return_value=True,
            ),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get("/trigger-error-ct")

        assert "application/json" in response.headers.get("content-type", "")
