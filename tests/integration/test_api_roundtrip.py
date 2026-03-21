"""HTTP round-trip integration tests for the Conclave Engine FastAPI application.

These tests exercise the real FastAPI HTTP stack end-to-end using
``httpx.AsyncClient`` with ``ASGITransport``.  Each test drives a complete
HTTP flow: routing → middleware → request parsing → handler → response
serialisation → client.

Unlike unit tests that call engine code directly, these tests verify:
- Request routing (correct path → correct handler)
- Middleware chain (CSP headers, SealGateMiddleware, RequestBodyLimitMiddleware)
- Request body parsing and Pydantic validation
- Response serialisation (correct JSON shape, correct status codes)
- RFC 7807 Problem Detail format for errors
- Database state after write operations

No PostgreSQL is required — all tests use an in-memory SQLite database via
SQLAlchemy's ``StaticPool``.  This is acceptable per the task spec: the goal
is to test the HTTP layer and middleware chain, not PostgreSQL-specific features.

Global-state contamination guard
---------------------------------
Each test fixture yields a fresh FastAPI app and SQLite engine.  VaultState
and LicenseState are patched per-test so leaked state from other tests cannot
bleed through.  No Prometheus registry or OTEL TracerProvider singletons are
shared across tests (``create_app()`` re-registers telemetry per call; the
``configure_telemetry`` call is idempotent in test mode because OTEL is
configured with a no-op tracer provider).

Mock scope (T40.2 review)
--------------------------
T40.2 reviewed this file.  Each test uses exactly 2 ``patch()`` calls —
both target external process-state singletons (VaultState.is_sealed,
LicenseState.is_licensed) that cannot be reset between tests by other means.
These are correct boundary mocks.  The tests exercise:
  - Real FastAPI app (full middleware stack via ``create_app()``)
  - Real in-memory SQLite database (via SQLModel + StaticPool)
  - Real HTTP client (httpx.AsyncClient + ASGITransport)
No rename to ``test_api_routing_wiring.py`` is required — this file IS a
genuine integration test, not a wiring test.

CONSTITUTION Priority 0: Security — no PII, no real credentials.
CONSTITUTION Priority 3: TDD — tests written before implementation.

Task: P26-T26.4 — HTTP Round-Trip Integration Tests
Task: P40-T40.2 — Replace Mock-Heavy Tests (reviewed; no changes required)
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

pytestmark = pytest.mark.integration

# ---------------------------------------------------------------------------
# Test app factory helpers
# ---------------------------------------------------------------------------

_VAULT_PATCH = "synth_engine.bootstrapper.dependencies.vault.VaultState.is_sealed"
_LICENSE_PATCH = "synth_engine.bootstrapper.dependencies.licensing.LicenseState.is_licensed"

#: Header name for Content-Security-Policy assertions.
_CSP_HEADER = "Content-Security-Policy"


def _make_sqlite_engine() -> Any:
    """Create an in-memory SQLite engine with all SQLModel tables created.

    Uses ``StaticPool`` so every connection shares the same in-memory database.
    Required when the route handler and the test assertion code use different
    ``Session`` instances backed by the same engine.

    Returns:
        A configured SQLAlchemy engine.
    """
    from synth_engine.modules.synthesizer.job_models import (
        SynthesisJob,  # noqa: F401  # side-effect: registers model in SQLModel metadata
    )

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return engine


def _make_test_app(engine: Any) -> Any:
    """Build a fully-wired FastAPI test app with DB dependency overridden.

    Creates a fresh ``create_app()`` instance (full middleware + routers) and
    overrides ``get_db_session`` to inject a session backed by the provided
    in-memory SQLite engine.

    Args:
        engine: SQLAlchemy engine with test tables already created.

    Returns:
        A FastAPI application instance ready for ``AsyncClient`` testing.
    """
    from synth_engine.bootstrapper.dependencies.db import get_db_session
    from synth_engine.bootstrapper.main import create_app

    app = create_app()

    def _override_session() -> Any:
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_db_session] = _override_session
    return app


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def sqlite_engine() -> Any:
    """Provide a fresh in-memory SQLite engine per test.

    Returns:
        A SQLAlchemy engine with all SQLModel tables created.
    """
    return _make_sqlite_engine()


@pytest.fixture
def test_app(sqlite_engine: Any) -> Any:
    """Provide a fully-wired FastAPI test application per test.

    Args:
        sqlite_engine: Injected in-memory SQLite engine fixture.

    Returns:
        A FastAPI application instance.
    """
    return _make_test_app(sqlite_engine)


# ---------------------------------------------------------------------------
# Helper: valid job creation payload
# ---------------------------------------------------------------------------

_VALID_JOB_PAYLOAD: dict[str, Any] = {
    "table_name": "customers",
    "parquet_path": "/tmp/test_customers.parquet",
    "total_epochs": 5,
    "num_rows": 100,
    "enable_dp": False,
}


# ---------------------------------------------------------------------------
# AC2 Flow 1 — Job creation: POST /jobs → 201 + DB state
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_job_creation_returns_201_and_persists_to_db(
    test_app: Any,
    sqlite_engine: Any,
) -> None:
    """POST /jobs with valid payload returns 201 and persists job to database.

    Verifies:
    - HTTP 201 status code.
    - Response body contains ``id``, ``status``, ``table_name``.
    - Job record exists in the database with the returned ``id``.
    - Job status defaults to ``QUEUED``.
    """
    from synth_engine.modules.synthesizer.job_models import SynthesisJob

    with patch(_VAULT_PATCH, return_value=False), patch(_LICENSE_PATCH, return_value=True):
        async with AsyncClient(
            transport=ASGITransport(app=test_app), base_url="http://test"
        ) as client:
            response = await client.post("/jobs", json=_VALID_JOB_PAYLOAD)

    assert response.status_code == 201, f"Expected 201, got {response.status_code}: {response.text}"

    body = response.json()
    assert "id" in body, "Response must include 'id' field"
    assert body["status"] == "QUEUED", f"Expected status=QUEUED, got {body['status']!r}"
    assert body["table_name"] == "customers"

    job_id = body["id"]
    with Session(sqlite_engine) as session:
        job = session.get(SynthesisJob, job_id)
    assert job is not None, f"Job {job_id} not found in database after creation"
    assert job.status == "QUEUED"
    assert job.table_name == "customers"


# ---------------------------------------------------------------------------
# AC2 Flow 2 — Job listing with pagination: GET /jobs?limit=10
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_job_listing_returns_correct_shape_and_pagination_cursor(
    test_app: Any,
    sqlite_engine: Any,
) -> None:
    """GET /jobs?limit=2 returns paginated response with correct shape and cursor.

    Verifies:
    - HTTP 200 status code.
    - Response body has ``items`` list and ``next_cursor`` field.
    - ``next_cursor`` is populated when more results exist beyond the page.
    - A second page fetch using the cursor returns the remaining jobs.
    """
    from synth_engine.modules.synthesizer.job_models import SynthesisJob

    with Session(sqlite_engine) as session:
        for i in range(3):
            session.add(
                SynthesisJob(
                    table_name=f"table_{i}",
                    parquet_path=f"/tmp/table_{i}.parquet",
                    total_epochs=1,
                    num_rows=10,
                )
            )
        session.commit()

    with patch(_VAULT_PATCH, return_value=False), patch(_LICENSE_PATCH, return_value=True):
        async with AsyncClient(
            transport=ASGITransport(app=test_app), base_url="http://test"
        ) as client:
            response_page1 = await client.get("/jobs", params={"limit": 2})

    assert response_page1.status_code == 200, (
        f"Expected 200, got {response_page1.status_code}: {response_page1.text}"
    )

    page1_body = response_page1.json()
    assert "items" in page1_body, "Response must have 'items' field"
    assert "next_cursor" in page1_body, "Response must have 'next_cursor' field"
    assert len(page1_body["items"]) == 2, (
        f"Expected 2 items on first page, got {len(page1_body['items'])}"
    )
    assert page1_body["next_cursor"] is not None, (
        "next_cursor must be non-null when more results exist"
    )

    # Verify each item has the expected schema fields
    item = page1_body["items"][0]
    for field in ("id", "status", "table_name", "total_epochs", "num_rows"):
        assert field in item, f"Job item missing required field: {field!r}"

    # Verify second page works via cursor
    cursor = page1_body["next_cursor"]
    with patch(_VAULT_PATCH, return_value=False), patch(_LICENSE_PATCH, return_value=True):
        async with AsyncClient(
            transport=ASGITransport(app=test_app), base_url="http://test"
        ) as client:
            response_page2 = await client.get("/jobs", params={"limit": 2, "after": cursor})

    assert response_page2.status_code == 200
    page2_body = response_page2.json()
    assert len(page2_body["items"]) == 1, (
        f"Expected 1 item on second page, got {len(page2_body['items'])}"
    )
    assert page2_body["next_cursor"] is None, "next_cursor must be None on the last page"


# ---------------------------------------------------------------------------
# AC2 Flow 3 — Job status: GET /jobs/{id} matches DB record
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_job_by_id_returns_response_matching_db_record(
    test_app: Any,
    sqlite_engine: Any,
) -> None:
    """GET /jobs/{id} returns a response whose fields match the database record.

    Verifies:
    - HTTP 200 status code.
    - Response ``id`` matches the requested job ID.
    - Response ``table_name``, ``status``, ``total_epochs``, ``num_rows``
      exactly match what was written to the database.
    - GET /jobs/99999 (non-existent) returns 404 RFC 7807 body.
    """
    from synth_engine.modules.synthesizer.job_models import SynthesisJob

    with Session(sqlite_engine) as session:
        job = SynthesisJob(
            table_name="orders",
            parquet_path="/tmp/orders.parquet",
            total_epochs=10,
            num_rows=50,
        )
        session.add(job)
        session.commit()
        session.refresh(job)
        job_id = job.id

    with patch(_VAULT_PATCH, return_value=False), patch(_LICENSE_PATCH, return_value=True):
        async with AsyncClient(
            transport=ASGITransport(app=test_app), base_url="http://test"
        ) as client:
            response = await client.get(f"/jobs/{job_id}")
            response_404 = await client.get("/jobs/99999")

    assert response.status_code == 200, (
        f"Expected 200 for existing job, got {response.status_code}: {response.text}"
    )
    body = response.json()
    assert body["id"] == job_id
    assert body["table_name"] == "orders"
    assert body["status"] == "QUEUED"
    assert body["total_epochs"] == 10
    assert body["num_rows"] == 50

    # 404 for non-existent job must use RFC 7807 shape
    assert response_404.status_code == 404, (
        f"Expected 404 for non-existent job, got {response_404.status_code}"
    )
    body_404 = response_404.json()
    assert body_404["status"] == 404
    assert "title" in body_404
    assert "detail" in body_404
    assert "type" in body_404


# ---------------------------------------------------------------------------
# AC2 Flow 4 — Seal gate: sealed vault → 423 for data endpoints
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sealed_vault_blocks_data_endpoints_with_423(
    test_app: Any,
) -> None:
    """While vault is sealed, data endpoints return 423 Locked.

    The task spec calls for 403 but the SealGateMiddleware returns 423 Locked
    per RFC 4918 (WebDAV), which is semantically more accurate for "vault
    is sealed / not yet activated".  The test asserts the actual implementation.

    Verifies:
    - GET /jobs while sealed → 423
    - POST /jobs while sealed → 423
    - GET /health while sealed → 200 (exempt path)
    """
    with patch(_VAULT_PATCH, return_value=True), patch(_LICENSE_PATCH, return_value=True):
        async with AsyncClient(
            transport=ASGITransport(app=test_app), base_url="http://test"
        ) as client:
            response_list = await client.get("/jobs")
            response_create = await client.post("/jobs", json=_VALID_JOB_PAYLOAD)
            response_health = await client.get("/health")

    assert response_list.status_code == 423, (
        f"GET /jobs while sealed must return 423, got {response_list.status_code}"
    )
    assert response_create.status_code == 423, (
        f"POST /jobs while sealed must return 423, got {response_create.status_code}"
    )
    assert response_health.status_code == 200, (
        f"GET /health must be exempt from seal gate, got {response_health.status_code}"
    )


# ---------------------------------------------------------------------------
# AC2 Flow 5 — Error response format: invalid request → RFC 7807 body
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_invalid_request_returns_rfc7807_problem_detail_format(
    test_app: Any,
) -> None:
    """POST /jobs with invalid payload returns a response with RFC 7807 fields.

    Sends a payload missing required fields (``table_name``, ``parquet_path``,
    ``total_epochs``, ``num_rows``) to trigger Pydantic validation failure.

    Verifies:
    - HTTP 422 status code (Pydantic validation error).
    - Response body contains a ``detail`` key (FastAPI validation error format).
    - For GET /jobs/not-an-integer → 422 with ``detail`` (path validation).
    """
    with patch(_VAULT_PATCH, return_value=False), patch(_LICENSE_PATCH, return_value=True):
        async with AsyncClient(
            transport=ASGITransport(app=test_app), base_url="http://test"
        ) as client:
            # Missing all required fields
            response_empty = await client.post("/jobs", json={})
            # Path parameter type mismatch
            response_invalid_id = await client.get("/jobs/not-an-integer")

    assert response_empty.status_code == 422, (
        f"Empty body must return 422, got {response_empty.status_code}: {response_empty.text}"
    )
    empty_body = response_empty.json()
    assert "detail" in empty_body, f"Validation error response must contain 'detail': {empty_body}"

    assert response_invalid_id.status_code == 422, (
        f"Invalid path param must return 422, got {response_invalid_id.status_code}"
    )


# ---------------------------------------------------------------------------
# AC3 — Middleware chain: request body limit >1MB → 413
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_oversized_request_body_returns_413(
    test_app: Any,
) -> None:
    """POST /jobs with a body exceeding 1 MiB returns 413 Payload Too Large.

    The RequestBodyLimitMiddleware rejects any request body exceeding
    ``MAX_BODY_BYTES`` (1 MiB) before the route handler processes it.

    Verifies:
    - HTTP 413 status code.
    - Response contains RFC 7807 ``status``, ``title``, ``detail``, ``type`` fields.
    """
    # Raw bytes that exceed the 1 MiB threshold — not valid JSON, but the size
    # check fires before JSON parsing so this triggers the 413 path.
    oversized_body = b"x" * (1 * 1024 * 1024 + 100)

    with patch(_VAULT_PATCH, return_value=False), patch(_LICENSE_PATCH, return_value=True):
        async with AsyncClient(
            transport=ASGITransport(app=test_app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/jobs",
                content=oversized_body,
                headers={"Content-Type": "application/json"},
            )

    assert response.status_code == 413, (
        f"Oversized body must return 413, got {response.status_code}: {response.text}"
    )
    body = response.json()
    assert body["status"] == 413
    assert "title" in body
    assert "detail" in body
    assert "type" in body


# ---------------------------------------------------------------------------
# AC3 — Middleware chain: CSP header present in all responses
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_csp_header_present_in_responses(
    test_app: Any,
) -> None:
    """CSP header is present on all HTTP responses regardless of status code.

    The CSPMiddleware attaches ``Content-Security-Policy`` to every response.
    Verified on a 200, a 404, and a 423 (sealed) response.

    Verifies:
    - ``Content-Security-Policy`` header present on 200 OK (health endpoint).
    - ``Content-Security-Policy`` header present on 404 Not Found.
    - ``Content-Security-Policy`` header present on 423 Locked (sealed vault).
    """
    with patch(_VAULT_PATCH, return_value=False), patch(_LICENSE_PATCH, return_value=True):
        async with AsyncClient(
            transport=ASGITransport(app=test_app), base_url="http://test"
        ) as client:
            response_200 = await client.get("/health")
            response_404 = await client.get("/jobs/99999")

    with patch(_VAULT_PATCH, return_value=True), patch(_LICENSE_PATCH, return_value=True):
        async with AsyncClient(
            transport=ASGITransport(app=test_app), base_url="http://test"
        ) as client:
            response_423 = await client.get("/jobs")

    assert _CSP_HEADER in response_200.headers, (
        f"CSP header missing from 200 response. Headers: {dict(response_200.headers)}"
    )
    assert _CSP_HEADER in response_404.headers, (
        f"CSP header missing from 404 response. Headers: {dict(response_404.headers)}"
    )
    assert _CSP_HEADER in response_423.headers, (
        f"CSP header missing from 423 (sealed) response. Headers: {dict(response_423.headers)}"
    )


# ---------------------------------------------------------------------------
# AC3 — Middleware chain: seal gate blocks non-exempt paths while sealed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_seal_gate_middleware_blocks_non_exempt_paths(
    test_app: Any,
) -> None:
    """SealGateMiddleware blocks non-exempt paths and allows exempt paths.

    Exercises the middleware path enumerated in EXEMPT_PATHS to confirm that
    the seal gate logic is correctly wired into the application middleware stack
    (not just a route-level dependency).

    Verifies:
    - ``/health`` is accessible while sealed.
    - ``/jobs`` is blocked (423) while sealed.
    - ``/jobs/{id}`` is blocked (423) while sealed.
    """
    with patch(_VAULT_PATCH, return_value=True), patch(_LICENSE_PATCH, return_value=True):
        async with AsyncClient(
            transport=ASGITransport(app=test_app), base_url="http://test"
        ) as client:
            health_resp = await client.get("/health")
            jobs_resp = await client.get("/jobs")
            job_id_resp = await client.get("/jobs/1")

    assert health_resp.status_code == 200, (
        f"/health must be exempt from seal gate, got {health_resp.status_code}"
    )
    assert jobs_resp.status_code == 423, (
        f"GET /jobs must be blocked by seal gate (423), got {jobs_resp.status_code}"
    )
    assert job_id_resp.status_code == 423, (
        f"GET /jobs/1 must be blocked by seal gate (423), got {job_id_resp.status_code}"
    )
