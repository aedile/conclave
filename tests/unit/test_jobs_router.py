"""Unit tests for the Jobs router (bootstrapper/routers/jobs.py).

Tests follow TDD RED phase — all tests must fail before implementation.

Task: P5-T5.1 — Task Orchestration API Core
Task: P22-T22.1 — Job Schema DP Parameters
CONSTITUTION Priority 3: TDD — RED phase
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlmodel import Session, SQLModel, create_engine

pytestmark = pytest.mark.unit


def _make_test_app() -> Any:
    """Build a test FastAPI app with an in-memory SQLite database."""
    from sqlalchemy.pool import StaticPool

    from synth_engine.bootstrapper.errors import register_error_handlers
    from synth_engine.bootstrapper.main import create_app
    from synth_engine.bootstrapper.routers.jobs import router as jobs_router
    from synth_engine.modules.synthesizer.jobs.job_models import SynthesisJob

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    # Seed a test job
    with Session(engine) as session:
        job = SynthesisJob(
            table_name="customers",
            parquet_path="/tmp/customers.parquet",
            total_epochs=10,
            num_rows=100,
        )
        session.add(job)
        session.commit()

    app = create_app()
    register_error_handlers(app)
    app.include_router(jobs_router)

    # Override the DB dependency to use the in-memory engine
    from synth_engine.bootstrapper.dependencies.db import get_db_session

    def _override_session() -> Any:
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_db_session] = _override_session
    return app, engine


class TestJobsListEndpoint:
    """Tests for GET /jobs cursor-based pagination."""

    @pytest.mark.asyncio
    async def test_list_jobs_returns_200(self) -> None:
        """GET /jobs must return HTTP 200."""
        app, engine = _make_test_app()

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
                response = await client.get("/api/v1/jobs")

        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_list_jobs_returns_items_list(self) -> None:
        """GET /jobs must return JSON with an 'items' list."""
        app, engine = _make_test_app()

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
                response = await client.get("/api/v1/jobs")

        body = response.json()
        assert "items" in body

    @pytest.mark.asyncio
    async def test_list_jobs_cursor_pagination_after(self) -> None:
        """GET /jobs?after=<cursor> must return jobs with id > cursor."""
        from sqlalchemy.pool import StaticPool

        from synth_engine.bootstrapper.dependencies.db import get_db_session
        from synth_engine.bootstrapper.errors import register_error_handlers
        from synth_engine.bootstrapper.main import create_app
        from synth_engine.bootstrapper.routers.jobs import router as jobs_router
        from synth_engine.modules.synthesizer.jobs.job_models import SynthesisJob

        engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        SQLModel.metadata.create_all(engine)

        with Session(engine) as session:
            for i in range(5):
                job = SynthesisJob(
                    table_name=f"table_{i}",
                    parquet_path=f"/tmp/t{i}.parquet",
                    total_epochs=10,
                    num_rows=100,
                )
                session.add(job)
            session.commit()

        app = create_app()
        register_error_handlers(app)
        app.include_router(jobs_router)

        def _override() -> Any:
            with Session(engine) as session:
                yield session

        app.dependency_overrides[get_db_session] = _override

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
                response = await client.get("/api/v1/jobs?after=2&limit=10")

        body = response.json()
        items = body["items"]
        for item in items:
            assert item["id"] > 2

    @pytest.mark.asyncio
    async def test_list_jobs_respects_limit(self) -> None:
        """GET /jobs?limit=2 must return at most 2 items."""
        from sqlalchemy.pool import StaticPool

        from synth_engine.bootstrapper.dependencies.db import get_db_session
        from synth_engine.bootstrapper.errors import register_error_handlers
        from synth_engine.bootstrapper.main import create_app
        from synth_engine.bootstrapper.routers.jobs import router as jobs_router
        from synth_engine.modules.synthesizer.jobs.job_models import SynthesisJob

        engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        SQLModel.metadata.create_all(engine)

        with Session(engine) as session:
            for i in range(5):
                job = SynthesisJob(
                    table_name=f"table_{i}",
                    parquet_path=f"/tmp/t{i}.parquet",
                    total_epochs=10,
                    num_rows=100,
                )
                session.add(job)
            session.commit()

        app = create_app()
        register_error_handlers(app)
        app.include_router(jobs_router)

        def _override() -> Any:
            with Session(engine) as session:
                yield session

        app.dependency_overrides[get_db_session] = _override

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
                response = await client.get("/api/v1/jobs?limit=2")

        body = response.json()
        assert len(body["items"]) <= 2

    @pytest.mark.asyncio
    async def test_list_jobs_response_includes_dp_fields(self) -> None:
        """GET /jobs items must include the DP parameter fields (P22-T22.1)."""
        app, engine = _make_test_app()

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
                response = await client.get("/api/v1/jobs")

        body = response.json()
        assert len(body["items"]) > 0
        item = body["items"][0]
        assert item["enable_dp"] is True
        assert item["noise_multiplier"] == 1.1
        assert item["max_grad_norm"] == 1.0
        assert item["actual_epsilon"] is None


class TestJobGetEndpoint:
    """Tests for GET /jobs/{id}."""

    @pytest.mark.asyncio
    async def test_get_job_returns_200_for_existing_job(self) -> None:
        """GET /jobs/{id} must return HTTP 200 for an existing job."""
        app, engine = _make_test_app()

        # Get the seeded job's id
        with Session(engine) as session:
            from sqlmodel import select

            from synth_engine.modules.synthesizer.jobs.job_models import SynthesisJob

            job = session.exec(select(SynthesisJob)).first()
            assert job is not None
            job_id = job.id

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
                response = await client.get(f"/api/v1/jobs/{job_id}")

        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_get_job_returns_404_for_missing_job(self) -> None:
        """GET /jobs/{id} must return HTTP 404 with RFC 7807 for missing job."""
        app, engine = _make_test_app()

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
                response = await client.get("/api/v1/jobs/99999")

        assert response.status_code == 404
        body = response.json()
        assert body.get("status") == 404
        assert "type" in body

    @pytest.mark.asyncio
    async def test_get_job_returns_correct_fields(self) -> None:
        """GET /jobs/{id} must return job fields including status, table_name."""
        app, engine = _make_test_app()

        with Session(engine) as session:
            from sqlmodel import select

            from synth_engine.modules.synthesizer.jobs.job_models import SynthesisJob

            job = session.exec(select(SynthesisJob)).first()
            assert job is not None
            job_id = job.id

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
                response = await client.get(f"/api/v1/jobs/{job_id}")

        body = response.json()
        assert "status" in body
        assert "table_name" in body
        assert body["table_name"] == "customers"

    @pytest.mark.asyncio
    async def test_get_job_response_includes_dp_fields(self) -> None:
        """GET /jobs/{id} response must include DP parameter fields (P22-T22.1)."""
        app, engine = _make_test_app()

        with Session(engine) as session:
            from sqlmodel import select

            from synth_engine.modules.synthesizer.jobs.job_models import SynthesisJob

            job = session.exec(select(SynthesisJob)).first()
            assert job is not None
            job_id = job.id

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
                response = await client.get(f"/api/v1/jobs/{job_id}")

        body = response.json()
        assert "enable_dp" in body
        assert "noise_multiplier" in body
        assert "max_grad_norm" in body
        assert "actual_epsilon" in body

    @pytest.mark.asyncio
    async def test_get_job_dp_defaults_match_spec(self) -> None:
        """GET /jobs/{id} must return the correct default DP field values (P22-T22.1)."""
        app, engine = _make_test_app()

        with Session(engine) as session:
            from sqlmodel import select

            from synth_engine.modules.synthesizer.jobs.job_models import SynthesisJob

            job = session.exec(select(SynthesisJob)).first()
            assert job is not None
            job_id = job.id

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
                response = await client.get(f"/api/v1/jobs/{job_id}")

        body = response.json()
        assert body["enable_dp"] is True
        assert body["noise_multiplier"] == 1.1
        assert body["max_grad_norm"] == 1.0
        assert body["actual_epsilon"] is None


class TestJobCreateEndpoint:
    """Tests for POST /jobs."""

    @pytest.mark.asyncio
    async def test_create_job_returns_201(self) -> None:
        """POST /jobs must return HTTP 201 Created."""
        app, engine = _make_test_app()

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
                response = await client.post(
                    "/api/v1/jobs",
                    json={
                        "table_name": "orders",
                        "parquet_path": "/tmp/orders.parquet",
                        "total_epochs": 5,
                        "num_rows": 100,
                    },
                )

        assert response.status_code == 201

    @pytest.mark.asyncio
    async def test_create_job_returns_created_job_body(self) -> None:
        """POST /jobs must return the newly created job body."""
        app, engine = _make_test_app()

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
                response = await client.post(
                    "/api/v1/jobs",
                    json={
                        "table_name": "orders",
                        "parquet_path": "/tmp/orders.parquet",
                        "total_epochs": 5,
                        "num_rows": 100,
                    },
                )

        body = response.json()
        assert body["table_name"] == "orders"
        assert body["status"] == "QUEUED"
        assert "id" in body

    @pytest.mark.asyncio
    async def test_create_job_dp_defaults_applied_when_omitted(self) -> None:
        """POST /jobs must apply DP defaults when DP params are not supplied (P22-T22.1)."""
        app, engine = _make_test_app()

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
                response = await client.post(
                    "/api/v1/jobs",
                    json={
                        "table_name": "orders",
                        "parquet_path": "/tmp/orders.parquet",
                        "total_epochs": 5,
                        "num_rows": 100,
                    },
                )

        assert response.status_code == 201
        body = response.json()
        assert body["enable_dp"] is True
        assert body["noise_multiplier"] == 1.1
        assert body["max_grad_norm"] == 1.0
        assert body["actual_epsilon"] is None

    @pytest.mark.asyncio
    async def test_create_job_accepts_custom_dp_params(self) -> None:
        """POST /jobs must persist custom DP params when supplied (P22-T22.1)."""
        app, engine = _make_test_app()

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
                response = await client.post(
                    "/api/v1/jobs",
                    json={
                        "table_name": "orders",
                        "parquet_path": "/tmp/orders.parquet",
                        "total_epochs": 5,
                        "num_rows": 100,
                        "enable_dp": False,
                        "noise_multiplier": 2.0,
                        "max_grad_norm": 0.5,
                    },
                )

        assert response.status_code == 201
        body = response.json()
        assert body["enable_dp"] is False
        assert body["noise_multiplier"] == 2.0
        assert body["max_grad_norm"] == 0.5

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("field", "value"),
        [
            pytest.param("noise_multiplier", 0.0, id="noise_multiplier_zero"),
            pytest.param("noise_multiplier", -1.0, id="noise_multiplier_negative"),
            pytest.param("noise_multiplier", 101, id="noise_multiplier_above_100"),
            pytest.param("max_grad_norm", 0.0, id="max_grad_norm_zero"),
            pytest.param("max_grad_norm", -0.1, id="max_grad_norm_negative"),
            pytest.param("max_grad_norm", 101, id="max_grad_norm_above_100"),
        ],
    )
    async def test_create_job_rejects_invalid_dp_param(self, field: str, value: float) -> None:
        """POST /jobs must return 422 for any degenerate DP parameter value.

        Zero and negative bounds make DP training mathematically invalid.
        Above-maximum values (> 100) are rejected as impractical configurations.
        All six cases must produce HTTP 422 Unprocessable Entity.

        Args:
            field: DP parameter field name (noise_multiplier or max_grad_norm).
            value: Invalid value for that field.
        """
        app, engine = _make_test_app()

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
                response = await client.post(
                    "/api/v1/jobs",
                    json={
                        "table_name": "orders",
                        "parquet_path": "/tmp/orders.parquet",
                        "total_epochs": 5,
                        "num_rows": 100,
                        field: value,
                    },
                )

        assert response.status_code == 422, (
            f"POST /jobs with {field}={value} expected 422, got {response.status_code}"
        )


class TestJobStartEndpoint:
    """Tests for POST /jobs/{id}/start."""

    @pytest.mark.asyncio
    async def test_start_job_returns_202(self) -> None:
        """POST /jobs/{id}/start must return HTTP 202 Accepted."""
        app, engine = _make_test_app()

        with Session(engine) as session:
            from sqlmodel import select

            from synth_engine.modules.synthesizer.jobs.job_models import SynthesisJob

            job = session.exec(select(SynthesisJob)).first()
            assert job is not None
            job_id = job.id

        with (
            patch(
                "synth_engine.bootstrapper.dependencies.vault.VaultState.is_sealed",
                return_value=False,
            ),
            patch(
                "synth_engine.bootstrapper.dependencies.licensing.LicenseState.is_licensed",
                return_value=True,
            ),
            patch("synth_engine.bootstrapper.routers.jobs.run_synthesis_job"),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(f"/api/v1/jobs/{job_id}/start")

        assert response.status_code == 202

    @pytest.mark.asyncio
    async def test_start_job_enqueues_huey_task(self) -> None:
        """POST /jobs/{id}/start must call run_synthesis_job with the job id."""
        app, engine = _make_test_app()

        with Session(engine) as session:
            from sqlmodel import select

            from synth_engine.modules.synthesizer.jobs.job_models import SynthesisJob

            job = session.exec(select(SynthesisJob)).first()
            assert job is not None
            job_id = job.id

        mock_task = MagicMock()
        with (
            patch(
                "synth_engine.bootstrapper.dependencies.vault.VaultState.is_sealed",
                return_value=False,
            ),
            patch(
                "synth_engine.bootstrapper.dependencies.licensing.LicenseState.is_licensed",
                return_value=True,
            ),
            patch(
                "synth_engine.bootstrapper.routers.jobs.run_synthesis_job",
                mock_task,
            ),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                await client.post(f"/api/v1/jobs/{job_id}/start")

        # T25.2: The dispatch site now passes trace_carrier as a keyword arg.
        # In this test context no OTEL span is active, so the carrier is empty.
        # F4: replaced tautological assert_called_once_with that read the actual
        # value to construct the expected value — the assertion proved nothing.
        mock_task.assert_called_once()
        assert mock_task.call_args.args == (job_id,), "start_job must pass job_id as positional arg"
        assert isinstance(mock_task.call_args.kwargs.get("trace_carrier"), dict), (
            "trace_carrier must be a dict (T25.2 AC2)"
        )

    @pytest.mark.asyncio
    async def test_start_nonexistent_job_returns_404(self) -> None:
        """POST /jobs/{id}/start must return 404 for a nonexistent job."""
        app, engine = _make_test_app()

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
                response = await client.post("/api/v1/jobs/99999/start")

        assert response.status_code == 404


class TestJobSSEEndpoint:
    """Tests for GET /jobs/{id}/stream SSE endpoint."""

    @pytest.mark.asyncio
    async def test_stream_nonexistent_job_returns_404(self) -> None:
        """GET /jobs/{id}/stream must return 404 for a nonexistent job."""
        app, engine = _make_test_app()

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
                response = await client.get("/api/v1/jobs/99999/stream")

        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_stream_completed_job_yields_complete_event(self) -> None:
        """GET /jobs/{id}/stream for a COMPLETE job must yield a complete event."""
        from sqlalchemy.pool import StaticPool

        from synth_engine.bootstrapper.dependencies.db import get_db_session
        from synth_engine.bootstrapper.errors import register_error_handlers
        from synth_engine.bootstrapper.main import create_app
        from synth_engine.bootstrapper.routers.jobs import router as jobs_router
        from synth_engine.modules.synthesizer.jobs.job_models import SynthesisJob

        engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        SQLModel.metadata.create_all(engine)

        with Session(engine) as session:
            job = SynthesisJob(
                table_name="t",
                parquet_path="/tmp/t.parquet",
                total_epochs=10,
                num_rows=100,
                status="COMPLETE",
                current_epoch=10,
            )
            session.add(job)
            session.commit()
            session.refresh(job)
            job_id = job.id

        app = create_app()
        register_error_handlers(app)
        app.include_router(jobs_router)

        def _override() -> Any:
            with Session(engine) as session:
                yield session

        app.dependency_overrides[get_db_session] = _override

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
                response = await client.get(f"/api/v1/jobs/{job_id}/stream")

        assert response.status_code == 200
        content = response.text
        assert "complete" in content

    @pytest.mark.asyncio
    async def test_stream_failed_job_yields_error_event(self) -> None:
        """GET /jobs/{id}/stream for a FAILED job must yield an error event."""
        from sqlalchemy.pool import StaticPool

        from synth_engine.bootstrapper.dependencies.db import get_db_session
        from synth_engine.bootstrapper.errors import register_error_handlers
        from synth_engine.bootstrapper.main import create_app
        from synth_engine.bootstrapper.routers.jobs import router as jobs_router
        from synth_engine.modules.synthesizer.jobs.job_models import SynthesisJob

        engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        SQLModel.metadata.create_all(engine)

        with Session(engine) as session:
            job = SynthesisJob(
                table_name="t",
                parquet_path="/tmp/t.parquet",
                total_epochs=10,
                num_rows=100,
                status="FAILED",
                error_msg="OOM error at /internal/path/to/code.py",
            )
            session.add(job)
            session.commit()
            session.refresh(job)
            job_id = job.id

        app = create_app()
        register_error_handlers(app)
        app.include_router(jobs_router)

        def _override() -> Any:
            with Session(engine) as session:
                yield session

        app.dependency_overrides[get_db_session] = _override

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
                response = await client.get(f"/api/v1/jobs/{job_id}/stream")

        assert response.status_code == 200
        content = response.text
        assert "error" in content
        # error_msg must be sanitized — path must not leak
        assert "/internal/path" not in content
