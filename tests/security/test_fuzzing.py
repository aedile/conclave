"""Security Fuzz Tests for the Conclave Engine API.

Validates that the API rejects malformed or adversarial inputs gracefully:

AC2: Nested JSON fuzz tests
- Deeply nested JSON (depth > 100) to POST /jobs is rejected with 400/413.
- Excessively large payloads (> 1 MB) are rejected with 413.
- Server does NOT crash or hang under these conditions.

AC3: NaN/Infinity float fuzz tests
- NaN and Infinity float values in POST /jobs (total_epochs, checkpoint_every_n)
  are rejected with 422 (Pydantic validation) rather than crashing the process.
- NaN/Infinity in the StatisticalProfiler are handled gracefully (no crash,
  finite or None output).

The ``RequestBodyLimitMiddleware`` must be added to ``bootstrapper/main.py``
for AC2 tests to pass.  The AC3 tests rely on Pydantic's built-in validation.

Guard against known failure patterns:
- [Pattern 3] No silent failures: all assertions are explicit
- [Pattern 6] VaultState test isolation: VaultState.reset() in every teardown
- [Pattern 7] HUEY_IMMEDIATE mode: set via os.environ
- [Pattern 9] No real PII: all test data is fictional
- [Pattern 10] Middleware placement: RequestBodyLimitMiddleware is outermost

CONSTITUTION Priority 0: Security — denial-of-service protection.
CONSTITUTION Priority 3: TDD — security gate for P6-T6.2.
Task: P6-T6.2 — NIST SP 800-88 Erasure, OWASP validation, LLM Fuzz Testing
"""

from __future__ import annotations

import json
import logging
import os
import warnings
from collections.abc import Generator
from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from synth_engine.shared.security.vault import VaultState

_logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Huey immediate mode for tests
# ---------------------------------------------------------------------------
os.environ.setdefault("HUEY_BACKEND", "memory")
os.environ.setdefault("HUEY_IMMEDIATE", "true")

# ---------------------------------------------------------------------------
# Size / depth limit constants (must match RequestBodyLimitMiddleware)
# ---------------------------------------------------------------------------

#: Maximum allowed request body size in bytes (1 MiB).
_MAX_BODY_BYTES: int = 1 * 1024 * 1024  # 1 MiB

#: Maximum allowed JSON nesting depth.
_MAX_JSON_DEPTH: int = 100


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_nested_json(depth: int) -> str:
    """Build a JSON string with the given nesting depth.

    Creates a structure like: {"a": {"a": {"a": ... {"value": 1} ...}}}
    The result is valid JSON but deeply nested.

    Args:
        depth: Number of nesting levels.

    Returns:
        JSON string with ``depth`` levels of nesting.
    """
    obj: dict[str, Any] = {"value": 1}
    for _ in range(depth - 1):
        obj = {"a": obj}
    return json.dumps(obj)


def _valid_job_payload() -> dict[str, Any]:
    """Return a minimal valid POST /jobs payload with fictional data.

    Returns:
        A dict suitable as a JSON body for POST /jobs.
    """
    return {
        "table_name": "fictional_users",
        "parquet_path": "/tmp/fictional_data.parquet",
        "total_epochs": 5,
        "checkpoint_every_n": 1,
    }


def _make_test_app() -> Any:
    """Build a FastAPI test app with SQLite in-memory DB.

    Overrides the DB dependency with an in-memory SQLite engine so tests
    run without a real PostgreSQL instance.

    Returns:
        The configured FastAPI application instance.
    """
    from synth_engine.bootstrapper.dependencies.db import get_db_session
    from synth_engine.bootstrapper.main import create_app

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    app = create_app()

    def _override_session() -> Generator[Session]:
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_db_session] = _override_session
    return app


# ---------------------------------------------------------------------------
# Vault teardown fixture
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_vault() -> Generator[None]:
    """Seal and clear vault KEK after every test (Pattern 6).

    Yields:
        Nothing — pure teardown.
    """
    yield
    VaultState.reset()


# ---------------------------------------------------------------------------
# Shared test client fixture with vault unsealed + license activated
# ---------------------------------------------------------------------------


@pytest.fixture
def app_client(
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[TestClient]:
    """Yield a TestClient with vault unsealed and license activated.

    Uses SQLite in-memory DB and patches the LicenseState and VaultState
    middlewares so routes are fully accessible without a real DB or vault.

    Args:
        monkeypatch: Pytest monkeypatch fixture for environment injection.

    Yields:
        A :class:`fastapi.testclient.TestClient` wrapping the test app.
    """
    import base64
    import secrets

    salt = base64.urlsafe_b64encode(secrets.token_bytes(16)).decode()
    monkeypatch.setenv("VAULT_SEAL_SALT", salt)
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")

    app = _make_test_app()

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
        with TestClient(app, raise_server_exceptions=False) as client:
            yield client


# ---------------------------------------------------------------------------
# AC2: Nested JSON Fuzz Tests
# ---------------------------------------------------------------------------


class TestNestedJsonFuzz:
    """Fuzz tests for deeply nested JSON payloads to POST /jobs."""

    @pytest.mark.unit
    def test_json_depth_101_rejected(self, app_client: TestClient) -> None:
        """JSON with depth 101 must be rejected (> MAX_DEPTH=100).

        The RequestBodyLimitMiddleware must inspect JSON depth and reject
        payloads exceeding the configured limit with 400 Bad Request.

        Args:
            app_client: TestClient fixture with unsealed vault and license activated.
        """
        body = _build_nested_json(depth=101)
        response = app_client.post(
            "/jobs",
            content=body,
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code in {400, 413}, (
            f"Expected 400 or 413 for JSON depth=101, got {response.status_code}. "
            "RequestBodyLimitMiddleware must reject deeply nested JSON."
        )
        _logger.info("JSON depth=101 correctly rejected with %d.", response.status_code)

    @pytest.mark.unit
    def test_json_depth_500_rejected(self, app_client: TestClient) -> None:
        """JSON with depth 500 must be rejected.

        Args:
            app_client: TestClient fixture with unsealed vault and license activated.
        """
        body = _build_nested_json(depth=500)
        response = app_client.post(
            "/jobs",
            content=body,
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code in {400, 413}, (
            f"Expected 400 or 413 for JSON depth=500, got {response.status_code}."
        )
        _logger.info("JSON depth=500 correctly rejected with %d.", response.status_code)

    @pytest.mark.unit
    def test_json_depth_1000_rejected(self, app_client: TestClient) -> None:
        """JSON with depth 1000 must be rejected without causing a stack overflow.

        Args:
            app_client: TestClient fixture with unsealed vault and license activated.
        """
        body = _build_nested_json(depth=1000)
        response = app_client.post(
            "/jobs",
            content=body,
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code in {400, 413}, (
            f"Expected 400 or 413 for JSON depth=1000, got {response.status_code}."
        )
        _logger.info("JSON depth=1000 correctly rejected with %d.", response.status_code)

    @pytest.mark.unit
    def test_json_depth_at_limit_not_rejected_by_depth_check(self, app_client: TestClient) -> None:
        """JSON at exactly MAX_DEPTH=100 must NOT be rejected by the depth check.

        A depth of exactly 100 should not trigger the limit.  The request
        will fail Pydantic validation (422) since the nested dict is not a
        valid JobCreateRequest — that is acceptable and expected.

        Args:
            app_client: TestClient fixture with unsealed vault and license activated.
        """
        body = _build_nested_json(depth=_MAX_JSON_DEPTH)
        response = app_client.post(
            "/jobs",
            content=body,
            headers={"Content-Type": "application/json"},
        )
        # Must NOT be 400 or 413 — depth=100 is at the limit, not over it.
        # A 400 here would indicate the depth check fired incorrectly.
        assert response.status_code not in {400, 413}, (
            "JSON at exactly MAX_DEPTH=100 must not trigger the size/depth rejection."
        )
        _logger.info(
            "JSON depth=%d at limit: status %d (not 400 or 413).",
            _MAX_JSON_DEPTH,
            response.status_code,
        )


class TestLargePayloadFuzz:
    """Fuzz tests for oversized request body payloads."""

    @pytest.mark.unit
    def test_payload_over_1mb_rejected(self, app_client: TestClient) -> None:
        """Payload exceeding 1 MiB must be rejected with 413 Payload Too Large.

        Args:
            app_client: TestClient fixture with unsealed vault and license activated.
        """
        # Build a payload just over 1 MiB
        oversized_body = "x" * (_MAX_BODY_BYTES + 1024)
        response = app_client.post(
            "/jobs",
            content=oversized_body,
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 413, (
            f"Expected 413 for payload > 1 MiB, got {response.status_code}. "
            "RequestBodyLimitMiddleware must reject oversized payloads."
        )
        _logger.info("Oversized payload correctly rejected with 413.")

    @pytest.mark.unit
    def test_payload_exactly_1mb_not_rejected_by_size(self, app_client: TestClient) -> None:
        """Payload of exactly 1 MiB must not be rejected by the size check alone.

        The content will fail JSON parsing, but must not hit the 413 gate.

        Args:
            app_client: TestClient fixture with unsealed vault and license activated.
        """
        exact_body = "x" * _MAX_BODY_BYTES
        response = app_client.post(
            "/jobs",
            content=exact_body,
            headers={"Content-Type": "application/json"},
        )
        # Must NOT be 400 or 413 — exactly 1 MiB is at the limit, not over it.
        # A 400 here would indicate the depth check fired on malformed JSON.
        assert response.status_code not in {400, 413}, (
            "Payload of exactly 1 MiB must not trigger the 413 size rejection."
        )
        _logger.info("Payload at exactly 1 MiB: status %d (not 400 or 413).", response.status_code)

    @pytest.mark.unit
    def test_server_remains_alive_after_oversized_payload(self, app_client: TestClient) -> None:
        """Server must continue to respond correctly after rejecting an oversized payload.

        Verifies that the middleware does not crash the server or corrupt
        its internal state.

        Args:
            app_client: TestClient fixture with unsealed vault and license activated.
        """
        # Send oversized payload
        oversized_body = "x" * (_MAX_BODY_BYTES + 1024)
        reject_response = app_client.post(
            "/jobs",
            content=oversized_body,
            headers={"Content-Type": "application/json"},
        )
        assert reject_response.status_code == 413

        # Server must still respond to health check
        health_response = app_client.get("/health")
        assert health_response.status_code == 200, (
            "Server must remain alive after rejecting an oversized payload."
        )
        _logger.info("Server alive after oversized payload rejection: PASS.")


# ---------------------------------------------------------------------------
# AC3: NaN/Infinity Float Fuzz Tests
# ---------------------------------------------------------------------------


class TestNanInfinityFuzz:
    """Fuzz tests for NaN and Infinity float values in API and profiler."""

    @pytest.mark.unit
    def test_nan_total_epochs_rejected(self, app_client: TestClient) -> None:
        """NaN as total_epochs must be rejected with 400, not a server crash.

        JSON does not natively support NaN as a value — sending it as a bare
        token produces a JSON parse error (400 Bad Request).  The server must
        not crash when receiving such input.

        Args:
            app_client: TestClient fixture with unsealed vault and license activated.
        """
        # NaN is not valid JSON — sends as a JSON parse error
        body = (
            '{"table_name": "fictional", "parquet_path": "/tmp/data.parquet",'
            ' "total_epochs": NaN, "checkpoint_every_n": 1}'
        )
        response = app_client.post(
            "/jobs",
            content=body,
            headers={"Content-Type": "application/json"},
        )
        # Must be 400 (JSON parse error) or 422 (Pydantic validation error)
        assert response.status_code in {400, 422}, (
            f"NaN in total_epochs must produce 400 or 422, got {response.status_code}. "
            "Server must not crash when receiving NaN."
        )
        _logger.info("NaN total_epochs correctly rejected with %d.", response.status_code)

    @pytest.mark.unit
    def test_infinity_total_epochs_rejected(self, app_client: TestClient) -> None:
        """Infinity as total_epochs must be rejected with 400, not a server crash.

        Args:
            app_client: TestClient fixture with unsealed vault and license activated.
        """
        # Infinity is not valid JSON — produces a parse error (400)
        body = (
            '{"table_name": "fictional", "parquet_path": "/tmp/data.parquet",'
            ' "total_epochs": Infinity, "checkpoint_every_n": 1}'
        )
        response = app_client.post(
            "/jobs",
            content=body,
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code in {400, 422}, (
            f"Infinity in total_epochs must produce 400 or 422, got {response.status_code}."
        )
        _logger.info("Infinity total_epochs correctly rejected with %d.", response.status_code)

    @pytest.mark.unit
    def test_nan_and_infinity_in_profiler_module(self) -> None:
        """NaN/Infinity passed to profiler functions must be handled gracefully.

        The StatisticalProfiler uses _safe_float() to sanitize NaN/Infinity
        at the output level.  This test exercises that path directly since
        no HTTP profiler endpoint currently exists.

        When NaN or Infinity are passed to _safe_float(), the function must
        return None rather than propagating the non-finite value.

        numpy may emit RuntimeWarning during quantile computation on Infinity
        data — this is expected behavior from a third-party library and is
        suppressed here.  The profiler's output sanitization via _safe_float
        ensures no non-finite values escape to callers.
        """
        import math

        import pandas as pd

        from synth_engine.modules.profiler.profiler import StatisticalProfiler, _safe_float

        # Direct function-level fuzz tests on _safe_float
        assert _safe_float(float("nan")) is None, "NaN must return None from _safe_float"
        assert _safe_float(float("inf")) is None, "Infinity must return None from _safe_float"
        assert _safe_float(float("-inf")) is None, (
            "Negative Infinity must return None from _safe_float"
        )
        assert _safe_float(42.0) == 42.0, "Normal float must pass through _safe_float"
        assert _safe_float(0.0) == 0.0, "Zero must pass through _safe_float"

        # Integration: profiler must not crash when given a DataFrame with NaN/Infinity.
        # numpy emits RuntimeWarning when computing percentiles on Infinity data —
        # this is third-party behavior we suppress here.
        df = pd.DataFrame(
            {
                "age": [25.0, float("nan"), float("inf"), 30.0],
                "score": [float("-inf"), 0.5, float("nan"), 0.9],
            }
        )
        profiler = StatisticalProfiler()

        # Suppress numpy RuntimeWarning from percentile computation on Infinity
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", category=RuntimeWarning, module="numpy")
            warnings.filterwarnings("ignore", category=RuntimeWarning, module="pandas")
            profile = profiler.profile("fictional_patients", df)

        # The profiler must return a valid profile (no exception raised)
        assert "age" in profile.columns, "age column must appear in the profile"
        assert "score" in profile.columns, "score column must appear in the profile"

        age_col = profile.columns["age"]
        assert age_col.is_numeric, "age must be classified as numeric"

        # Verify that all numeric stats are either None or finite (no NaN/Inf output)
        for stat_name in ("mean", "stddev", "min", "max", "q25", "q50", "q75"):
            stat_val = getattr(age_col, stat_name)
            if stat_val is not None:
                assert math.isfinite(stat_val), (
                    f"Profiler stat '{stat_name}' must be finite or None, got {stat_val}"
                )

        _logger.info("Profiler NaN/Infinity fuzz test: all output stats are finite or None. PASS.")

    @pytest.mark.unit
    def test_nan_via_json_null_is_rejected(self, app_client: TestClient) -> None:
        """NaN represented as JSON null in total_epochs must be rejected by Pydantic.

        ``null`` is valid JSON but total_epochs is typed as ``int`` (not Optional).
        Pydantic must reject this with 422 Unprocessable Entity.

        Args:
            app_client: TestClient fixture with unsealed vault and license activated.
        """
        payload: dict[str, Any] = {
            "table_name": "fictional_users",
            "parquet_path": "/tmp/fictional_data.parquet",
            "total_epochs": None,  # null — not a valid int
            "checkpoint_every_n": 1,
        }
        response = app_client.post("/jobs", json=payload)
        assert response.status_code == 422, (
            f"null total_epochs must be rejected with 422, got {response.status_code}."
        )
        _logger.info("null total_epochs correctly rejected with 422.")

    @pytest.mark.unit
    def test_zero_total_epochs_rejected_by_pydantic(self, app_client: TestClient) -> None:
        """total_epochs=0 must be rejected (gt=0 constraint in Pydantic schema).

        This tests the boundary condition: epoch count of 0 would cause a
        division-by-zero in training loops.

        Args:
            app_client: TestClient fixture with unsealed vault and license activated.
        """
        payload: dict[str, Any] = {
            "table_name": "fictional_users",
            "parquet_path": "/tmp/fictional_data.parquet",
            "total_epochs": 0,
            "checkpoint_every_n": 1,
        }
        response = app_client.post("/jobs", json=payload)
        assert response.status_code == 422, (
            f"total_epochs=0 must be rejected with 422, got {response.status_code}."
        )
        _logger.info("total_epochs=0 correctly rejected with 422.")

    @pytest.mark.unit
    def test_very_large_integer_total_epochs_handled_without_crash(
        self, app_client: TestClient
    ) -> None:
        """An astronomically large total_epochs must be handled without crashing.

        Python handles big integers natively but the DB layer may have limits.
        The server must respond (not crash) — any HTTP status is acceptable.

        Args:
            app_client: TestClient fixture with unsealed vault and license activated.
        """
        payload: dict[str, Any] = {
            "table_name": "fictional_users",
            "parquet_path": "/tmp/fictional_data.parquet",
            "total_epochs": 10**18,  # 1 quintillion
            "checkpoint_every_n": 1,
        }
        response = app_client.post("/jobs", json=payload)
        # Server must respond (not crash) — status can be 201, 400, 422, or 500
        assert response.status_code in {201, 400, 422, 500}, (
            f"Very large total_epochs must not crash server, got status {response.status_code}."
        )
        _logger.info("Very large total_epochs: status %d (server alive).", response.status_code)
