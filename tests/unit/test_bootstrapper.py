"""Unit tests for the FastAPI application bootstrapper.

Tests for the create_app() factory function, health endpoint, and
basic application structure.

CONSTITUTION Priority 3: TDD RED Phase
Task: P2-T2.1 — Module Bootstrapper, OTEL, Idempotency, Orphan Task Reaper
Task: P3.5-T3.5.4 — Bootstrapper Wiring & Minimal CLI Entrypoint
Task: P4-T4.2b — SynthesisEngine + EphemeralStorageClient factory wiring
                  (ADV-037 drain)
"""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient


@pytest.mark.asyncio
async def test_health_endpoint_returns_200() -> None:
    """GET /health returns HTTP 200 with status ok body.

    The health endpoint is the minimal liveness probe for the service.
    It must return a 200 with a JSON body containing {"status": "ok"}.
    """
    from synth_engine.bootstrapper.main import create_app

    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_app_is_fastapi_instance() -> None:
    """create_app() must return a FastAPI instance, not a module-level singleton.

    The factory pattern allows test isolation — each call creates a
    fresh application with no shared state.
    """
    from synth_engine.bootstrapper.main import create_app

    assert isinstance(create_app(), FastAPI)


def test_create_app_returns_new_instance_each_call() -> None:
    """create_app() must return a new FastAPI instance on each invocation.

    This ensures test isolation and prevents shared state between
    different call sites.
    """
    from synth_engine.bootstrapper.main import create_app

    app1 = create_app()
    app2 = create_app()

    assert app1 is not app2


# ---------------------------------------------------------------------------
# CycleDetectionError → 422 RFC 7807
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cycle_detection_error_returns_422_rfc7807() -> None:
    """CycleDetectionError raised by a subsetting engine handler returns HTTP 422.

    The bootstrapper must intercept CycleDetectionError (ADV-022) and
    return an RFC 7807 Problem Details response with status 422, not 500.

    RFC 7807 required fields: type, title, status, detail.

    The vault is patched to the unsealed state so the SealGateMiddleware
    does not intercept the test route with a 423.  The license is patched
    to the activated state so the LicenseGateMiddleware does not intercept
    with a 402.
    """
    from synth_engine.bootstrapper.main import create_app
    from synth_engine.modules.mapping import CycleDetectionError

    app = create_app()

    # Register a test route that raises CycleDetectionError so we can verify
    # the exception handler is wired correctly.
    # CycleDetectionError takes a list[str] cycle path — not a bare string.
    @app.get("/test-cycle-error")
    async def _trigger_cycle_error() -> None:
        raise CycleDetectionError(["table_a", "table_b", "table_a"])

    # Patch the vault seal check so SealGateMiddleware allows the request.
    # Patch the license check so LicenseGateMiddleware allows the request.
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
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/test-cycle-error")

    assert response.status_code == 422
    body = response.json()

    # RFC 7807 required fields
    assert body.get("status") == 422
    assert "title" in body
    assert "detail" in body
    assert "type" in body
    # The detail must carry a meaningful cycle description
    assert "table_a" in body["detail"]


@pytest.mark.asyncio
async def test_cycle_detection_error_not_a_500() -> None:
    """CycleDetectionError must never produce HTTP 500.

    A generic unhandled exception produces 500. This test verifies the
    bootstrapper's exception handler intercepts CycleDetectionError before
    FastAPI's default 500 handler fires.
    """
    from synth_engine.bootstrapper.main import create_app
    from synth_engine.modules.mapping import CycleDetectionError

    app = create_app()

    @app.get("/test-cycle-not-500")
    async def _raise_cycle() -> None:
        raise CycleDetectionError(["orders", "line_items", "orders"])

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
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/test-cycle-not-500")

    assert response.status_code != 500


# ---------------------------------------------------------------------------
# ADV-037 drain: _read_secret, build_synthesis_engine, build_ephemeral_storage_client
# ---------------------------------------------------------------------------


class TestReadSecret:
    """Unit tests for the _read_secret() Docker secrets helper."""

    def test_reads_secret_from_file(self) -> None:
        """_read_secret() must read and strip the content of a secrets file."""
        from synth_engine.bootstrapper.main import _read_secret

        with tempfile.TemporaryDirectory() as tmpdir:
            secret_path = Path(tmpdir) / "my_secret"
            secret_path.write_text("supersecretvalue\n", encoding="utf-8")

            with patch("synth_engine.bootstrapper.main._SECRETS_DIR", Path(tmpdir)):
                result = _read_secret("my_secret")

        assert result == "supersecretvalue"

    def test_raises_runtime_error_for_missing_secret(self) -> None:
        """_read_secret() must raise RuntimeError if the secret file is absent."""
        from synth_engine.bootstrapper.main import _read_secret

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("synth_engine.bootstrapper.main._SECRETS_DIR", Path(tmpdir)):
                with pytest.raises(RuntimeError, match="not found"):
                    _read_secret("nonexistent_secret")

    def test_strips_trailing_whitespace(self) -> None:
        """_read_secret() must strip trailing whitespace and newlines."""
        from synth_engine.bootstrapper.main import _read_secret

        with tempfile.TemporaryDirectory() as tmpdir:
            secret_path = Path(tmpdir) / "padded_secret"
            secret_path.write_text("  myvalue  \n\n", encoding="utf-8")

            with patch("synth_engine.bootstrapper.main._SECRETS_DIR", Path(tmpdir)):
                result = _read_secret("padded_secret")

        assert result == "myvalue"


class TestBuildSynthesisEngine:
    """Unit tests for the build_synthesis_engine() bootstrapper factory."""

    def test_returns_synthesis_engine_instance(self) -> None:
        """build_synthesis_engine() must return a SynthesisEngine instance."""
        from synth_engine.bootstrapper.main import build_synthesis_engine
        from synth_engine.modules.synthesizer.engine import SynthesisEngine

        engine = build_synthesis_engine()
        assert isinstance(engine, SynthesisEngine)

    def test_default_epochs_is_300(self) -> None:
        """build_synthesis_engine() default produces an engine with 300 epochs."""
        from synth_engine.bootstrapper.main import build_synthesis_engine

        engine = build_synthesis_engine()
        # Access private _epochs attribute to verify epoch count
        assert engine._epochs == 300

    def test_custom_epochs_passed_through(self) -> None:
        """build_synthesis_engine(epochs=N) must produce engine with N epochs."""
        from synth_engine.bootstrapper.main import build_synthesis_engine

        engine = build_synthesis_engine(epochs=5)
        assert engine._epochs == 5

    def test_returns_fresh_instance_each_call(self) -> None:
        """build_synthesis_engine() must return a new instance on each call."""
        from synth_engine.bootstrapper.main import build_synthesis_engine

        engine1 = build_synthesis_engine()
        engine2 = build_synthesis_engine()
        assert engine1 is not engine2


class TestBuildEphemeralStorageClient:
    """Unit tests for the build_ephemeral_storage_client() bootstrapper factory.

    MinioStorageBackend is not instantiated directly — boto3.client() is
    patched to avoid real network calls to MinIO.
    """

    def _make_secret_files(self, tmpdir: str) -> Path:
        """Create mock secret files in a temporary directory.

        Args:
            tmpdir: Path to a temporary directory.

        Returns:
            Path to the directory containing the mock secret files.
        """
        secrets_dir = Path(tmpdir)
        (secrets_dir / "minio_ephemeral_access_key").write_text("testkey\n", encoding="utf-8")
        (secrets_dir / "minio_ephemeral_secret_key").write_text("testsecret\n", encoding="utf-8")
        return secrets_dir

    def test_returns_ephemeral_storage_client_instance(self) -> None:
        """build_ephemeral_storage_client() must return an EphemeralStorageClient."""
        from synth_engine.bootstrapper.main import build_ephemeral_storage_client
        from synth_engine.modules.synthesizer.storage import EphemeralStorageClient

        with tempfile.TemporaryDirectory() as tmpdir:
            secrets_dir = self._make_secret_files(tmpdir)
            # Patch boto3.client to prevent real network calls
            with (
                patch("synth_engine.bootstrapper.main._SECRETS_DIR", secrets_dir),
                patch("boto3.client", return_value=MagicMock()),
            ):
                result = build_ephemeral_storage_client()

        assert isinstance(result, EphemeralStorageClient)

    def test_reads_credentials_from_docker_secrets(self) -> None:
        """build_ephemeral_storage_client() must pass secrets to MinioStorageBackend.

        Verifies that access_key and secret_key are read from Docker secrets
        and forwarded to the MinioStorageBackend constructor (stripped of
        trailing whitespace).
        """
        from synth_engine.bootstrapper.main import build_ephemeral_storage_client

        with tempfile.TemporaryDirectory() as tmpdir:
            secrets_dir = Path(tmpdir)
            (secrets_dir / "minio_ephemeral_access_key").write_text(
                "myaccesskey\n", encoding="utf-8"
            )
            (secrets_dir / "minio_ephemeral_secret_key").write_text(
                "mysecretkey\n", encoding="utf-8"
            )

            with (
                patch("synth_engine.bootstrapper.main._SECRETS_DIR", secrets_dir),
                patch("boto3.client") as mock_boto3_client,
            ):
                mock_boto3_client.return_value = MagicMock()
                build_ephemeral_storage_client()

            # Verify boto3.client was called with the stripped credentials
            mock_boto3_client.assert_called_once_with(
                "s3",
                endpoint_url="http://minio-ephemeral:9000",
                aws_access_key_id="myaccesskey",
                aws_secret_access_key="mysecretkey",  # pragma: allowlist secret
            )

    def test_raises_runtime_error_when_secrets_missing(self) -> None:
        """build_ephemeral_storage_client() must raise RuntimeError if secrets absent."""
        from synth_engine.bootstrapper.main import build_ephemeral_storage_client

        with tempfile.TemporaryDirectory() as tmpdir:
            # Empty secrets dir — no secret files
            with patch("synth_engine.bootstrapper.main._SECRETS_DIR", Path(tmpdir)):
                with pytest.raises(RuntimeError, match="not found"):
                    build_ephemeral_storage_client()


# ---------------------------------------------------------------------------
# Pytest mark
# ---------------------------------------------------------------------------

pytestmark = pytest.mark.unit
