"""Additional coverage gap tests for P81 CI fix — second batch.

Targets uncovered lines after the first batch (test_p81_coverage_gaps.py):
- config_validation.py: lines 385-410, 438-442 (_validate_oidc_config, _warn_if_oidc_...)
- dependencies/sessions.py: lines 250, 261-262, 265-266, 272
- lifecycle.py: lines 89-90, 138-139 (prometheus error path, vault audit skip)
- modules/synthesizer/storage/retention.py: lines 228-239, 260-261 (exception paths)

All tests exercise legitimate error-handling paths and edge cases that were not
reached by existing tests.

CONSTITUTION Priority 0: Security — startup validation and session management error
paths must be tested.
CONSTITUTION Priority 3: TDD — coverage gate requires 95% minimum.
Task: P81-CI-FIX — resolve coverage gap after OIDC integration.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from synth_engine.modules.synthesizer.jobs.job_models import SynthesisJob

pytestmark = pytest.mark.unit


# ===========================================================================
# config_validation.py — _validate_oidc_config coverage (lines 385-410)
# ===========================================================================


class TestValidateOIDCConfig:
    """Lines 385-410: _validate_oidc_config body when OIDC is enabled but fields are missing.

    These tests call _validate_oidc_config directly with a mock settings object
    to avoid triggering unrelated ConclaveSettings validators.
    """

    def _make_oidc_settings(
        self,
        *,
        oidc_enabled: bool = True,
        oidc_issuer_url: str = "https://idp.example.com/realms/test",
        oidc_client_id: str = "my-client",
        oidc_client_secret: str = "my-secret",  # noqa: S107
        redis_url: str = "redis://localhost:6379",
        conclave_multi_tenant_enabled: bool = False,
        oidc_default_org_id: str | None = "org-id",
    ) -> MagicMock:
        """Build a mock settings object for OIDC config tests."""
        from pydantic import SecretStr

        mock = MagicMock()
        mock.oidc_enabled = oidc_enabled
        mock.oidc_issuer_url = oidc_issuer_url
        mock.oidc_client_id = oidc_client_id
        mock.oidc_client_secret = SecretStr(oidc_client_secret)
        mock.redis_url = redis_url
        mock.conclave_multi_tenant_enabled = conclave_multi_tenant_enabled
        mock.oidc_default_org_id = oidc_default_org_id
        return mock

    def test_oidc_disabled_no_errors_appended(self) -> None:
        """When OIDC_ENABLED=false, _validate_oidc_config returns immediately with no errors.

        Lines 382-383: the `if not settings.oidc_enabled: return` guard.
        """
        from synth_engine.bootstrapper.config_validation import _validate_oidc_config

        mock_settings = self._make_oidc_settings(oidc_enabled=False)
        errors: list[str] = []

        with patch(
            "synth_engine.bootstrapper.config_validation.get_settings",
            return_value=mock_settings,
        ):
            _validate_oidc_config(errors)

        assert errors == [], f"Expected no errors when OIDC disabled, got: {errors}"

    def test_oidc_enabled_missing_issuer_url_appends_error(self) -> None:
        """Error appended when OIDC_ENABLED=true but OIDC_ISSUER_URL is empty.

        Lines 385-389: the `if not settings.oidc_issuer_url` check.
        """
        from synth_engine.bootstrapper.config_validation import _validate_oidc_config

        mock_settings = self._make_oidc_settings(oidc_issuer_url="")
        errors: list[str] = []

        with patch(
            "synth_engine.bootstrapper.config_validation.get_settings",
            return_value=mock_settings,
        ):
            _validate_oidc_config(errors)

        assert any("OIDC_ISSUER_URL" in e for e in errors), (
            f"Expected error about OIDC_ISSUER_URL, got: {errors}"
        )

    def test_oidc_enabled_missing_client_id_appends_error(self) -> None:
        """Error appended when OIDC_ENABLED=true but OIDC_CLIENT_ID is empty.

        Lines 391-395: the `if not settings.oidc_client_id` check.
        """
        from synth_engine.bootstrapper.config_validation import _validate_oidc_config

        mock_settings = self._make_oidc_settings(oidc_client_id="")
        errors: list[str] = []

        with patch(
            "synth_engine.bootstrapper.config_validation.get_settings",
            return_value=mock_settings,
        ):
            _validate_oidc_config(errors)

        assert any("OIDC_CLIENT_ID" in e for e in errors), (
            f"Expected error about OIDC_CLIENT_ID, got: {errors}"
        )

    def test_oidc_enabled_missing_client_secret_appends_error(self) -> None:
        """Error appended when OIDC_ENABLED=true but OIDC_CLIENT_SECRET is empty.

        Lines 397-402: the `if not client_secret` check.
        """
        from synth_engine.bootstrapper.config_validation import _validate_oidc_config

        mock_settings = self._make_oidc_settings(oidc_client_secret="")
        errors: list[str] = []

        with patch(
            "synth_engine.bootstrapper.config_validation.get_settings",
            return_value=mock_settings,
        ):
            _validate_oidc_config(errors)

        assert any("OIDC_CLIENT_SECRET" in e for e in errors), (
            f"Expected error about OIDC_CLIENT_SECRET, got: {errors}"
        )

    def test_oidc_enabled_missing_redis_url_appends_error(self) -> None:
        """Error appended when OIDC_ENABLED=true but REDIS_URL is empty.

        Lines 404-407: the `if not settings.redis_url` check.
        """
        from synth_engine.bootstrapper.config_validation import _validate_oidc_config

        mock_settings = self._make_oidc_settings(redis_url="")
        errors: list[str] = []

        with patch(
            "synth_engine.bootstrapper.config_validation.get_settings",
            return_value=mock_settings,
        ):
            _validate_oidc_config(errors)

        assert any("REDIS_URL" in e for e in errors), (
            f"Expected error about REDIS_URL, got: {errors}"
        )

    def test_oidc_enabled_multi_tenant_missing_org_id_appends_error(self) -> None:
        """Error appended when OIDC+multi-tenant mode but OIDC_DEFAULT_ORG_ID is missing.

        Lines 409-414: the `if settings.conclave_multi_tenant_enabled and not ...` check.
        """
        from synth_engine.bootstrapper.config_validation import _validate_oidc_config

        mock_settings = self._make_oidc_settings(
            conclave_multi_tenant_enabled=True,
            oidc_default_org_id=None,
        )
        errors: list[str] = []

        with patch(
            "synth_engine.bootstrapper.config_validation.get_settings",
            return_value=mock_settings,
        ):
            _validate_oidc_config(errors)

        assert any("OIDC_DEFAULT_ORG_ID" in e for e in errors), (
            f"Expected error about OIDC_DEFAULT_ORG_ID, got: {errors}"
        )

    def test_oidc_all_valid_no_errors(self) -> None:
        """No errors appended when OIDC is enabled and all required fields are set.

        Verifies the happy path — all branches missed and no error appended.
        """
        from synth_engine.bootstrapper.config_validation import _validate_oidc_config

        mock_settings = self._make_oidc_settings()
        errors: list[str] = []

        with patch(
            "synth_engine.bootstrapper.config_validation.get_settings",
            return_value=mock_settings,
        ):
            _validate_oidc_config(errors)

        assert errors == [], f"Expected no errors for fully configured OIDC, got: {errors}"


# ===========================================================================
# config_validation.py — _warn_if_oidc_client_secret_env_in_production (lines 438-442)
# ===========================================================================


class TestWarnIfOIDCClientSecretEnvInProduction:
    """Lines 438-442: _warn_if_oidc_client_secret_env_in_production CRITICAL log."""

    def test_oidc_secret_via_env_in_production_emits_critical_log(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """CRITICAL log emitted when OIDC_CLIENT_SECRET is set as env var in production.

        Lines 438-442: the `if client_secret and not docker_secret_path.exists()` branch.
        Called directly to avoid triggering unrelated ConclaveSettings validators.
        """
        from pydantic import SecretStr

        from synth_engine.bootstrapper.config_validation import (
            _warn_if_oidc_client_secret_env_in_production,
        )

        mock_settings = MagicMock()
        mock_settings.oidc_enabled = True
        mock_settings.oidc_client_secret = SecretStr("my-secret")  # non-empty
        mock_settings.is_production.return_value = True

        # Patch: docker secret file does NOT exist, client_secret is set as env var
        with patch(
            "synth_engine.bootstrapper.config_validation.get_settings",
            return_value=mock_settings,
        ):
            with patch(
                "synth_engine.bootstrapper.config_validation._is_production",
                return_value=True,
            ):
                # Patch the Path inside the function: the lazy import means we patch
                # synth_engine.bootstrapper.config_validation module-level Path via
                # the pathlib module itself, but scoped only to the .exists() call on
                # the docker_secret_path. We use a targeted patch on pathlib.Path.exists.
                # To avoid breaking CONCLAVE_DATA_DIR check we call the function directly
                # (not through validate_config), so Path.exists is only called once.
                with patch("pathlib.Path.exists", return_value=False):
                    with caplog.at_level(
                        logging.CRITICAL,
                        logger="synth_engine.bootstrapper.config_validation",
                    ):
                        _warn_if_oidc_client_secret_env_in_production()

        critical_messages = [r.message for r in caplog.records if r.levelno == logging.CRITICAL]
        assert any("OIDC_CLIENT_SECRET" in msg for msg in critical_messages), (
            f"Expected CRITICAL log about OIDC_CLIENT_SECRET env var, got: {critical_messages}"
        )
        assert any("Docker" in msg or "docker" in msg for msg in critical_messages), (
            "CRITICAL log must mention Docker secrets as the correct approach"
        )

    def test_oidc_not_enabled_no_critical_log(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """No CRITICAL log emitted when OIDC is not enabled.

        Line 435: `if not settings.oidc_enabled or not _is_production(): return`
        guard when oidc_enabled=False.
        """
        from synth_engine.bootstrapper.config_validation import (
            _warn_if_oidc_client_secret_env_in_production,
        )

        mock_settings = MagicMock()
        mock_settings.oidc_enabled = False

        with patch(
            "synth_engine.bootstrapper.config_validation.get_settings",
            return_value=mock_settings,
        ):
            with caplog.at_level(
                logging.CRITICAL,
                logger="synth_engine.bootstrapper.config_validation",
            ):
                _warn_if_oidc_client_secret_env_in_production()

        critical_messages = [r.message for r in caplog.records if r.levelno == logging.CRITICAL]
        assert critical_messages == [], (
            f"Expected no CRITICAL log when OIDC disabled, got: {critical_messages}"
        )


# ===========================================================================
# dependencies/sessions.py — concurrent session limit edge cases
# ===========================================================================


class TestEnforceConcurrentSessionLimit:
    """Edge-case coverage for enforce_concurrent_session_limit."""

    def test_empty_session_set_returns_immediately(self) -> None:
        """When no session keys exist for a user, function returns without Redis ops.

        Line 250: the `if not session_keys: return` guard — short-circuit when
        the per-user session index is empty (user has no active sessions).
        """
        from synth_engine.bootstrapper.dependencies.sessions import (
            enforce_concurrent_session_limit,
        )

        redis_client = MagicMock()
        redis_client.smembers.return_value = set()  # empty set

        # Must not raise and must not call mget when session_keys is empty
        enforce_concurrent_session_limit(
            redis_client=redis_client,
            user_id="test-user-id",
            org_id="test-org-id",
            limit=3,
        )

        redis_client.mget.assert_not_called()
        # Strong assertion: mget call count is exactly 0 (no work was done)
        assert redis_client.mget.call_count == 0, (
            f"Expected mget call count 0 for empty session set, got: {redis_client.mget.call_count}"
        )

    def test_expired_key_added_to_stale_and_cleaned_up(self) -> None:
        """Expired session key (raw=None from Redis) is cleaned up via srem.

        Lines 259-261: `if raw is None: stale_keys.append(key); continue` —
        hit when a Redis key has expired between SMEMBERS and MGET.
        Line 272: `redis_client.srem(index_key, *stale_keys)` — cleanup call.
        """
        from synth_engine.bootstrapper.dependencies.sessions import (
            enforce_concurrent_session_limit,
        )

        redis_client = MagicMock()
        expired_key = b"conclave:session:expired-token"
        valid_key = b"conclave:session:valid-token"

        # Use only the expired key so ordering is deterministic (single-element set)
        redis_client.smembers.return_value = {expired_key}
        # mget returns None for the expired key (TTL elapsed between SMEMBERS and MGET)
        redis_client.mget.return_value = [None]

        enforce_concurrent_session_limit(
            redis_client=redis_client,
            user_id="test-user-id",
            org_id="test-org-id",
            limit=5,  # high limit — no eviction needed
        )

        # srem must have been called to clean up the expired stale key
        redis_client.srem.assert_called()
        # Collect all srem positional args (excluding the index_key first arg)
        srem_args_flat = [arg for call in redis_client.srem.call_args_list for arg in call[0][1:]]
        assert expired_key in srem_args_flat, (
            f"Expected expired_key to be removed via srem, srem calls: "
            f"{redis_client.srem.call_args_list}"
        )
        # valid_key is not in this test — ensuring no eviction happened
        assert valid_key not in srem_args_flat, (
            "valid_key must not be srem'd in a stale-cleanup-only scenario"
        )

    def test_json_decode_error_session_skipped(self) -> None:
        """A session key whose value is malformed JSON is silently skipped.

        Lines 265-266: the `except (json.JSONDecodeError, ValueError): continue` clause
        inside enforce_concurrent_session_limit — hit when Redis returns a value
        that is not valid JSON.
        """
        from synth_engine.bootstrapper.dependencies.sessions import (
            enforce_concurrent_session_limit,
        )

        redis_client = MagicMock()
        bad_key = b"conclave:session:corrupt-token"

        redis_client.smembers.return_value = {bad_key}
        redis_client.mget.return_value = [b"not-valid-json-{{{"]  # malformed JSON

        # Must not raise — corrupt session data is skipped silently
        enforce_concurrent_session_limit(
            redis_client=redis_client,
            user_id="test-user-id",
            org_id="test-org-id",
            limit=1,
        )

        # No eviction should occur since user_sessions list remains empty
        redis_client.delete.assert_not_called()
        # Strong assertion: delete call count is exactly 0 (no session evicted)
        assert redis_client.delete.call_count == 0, (
            f"Expected delete call count 0 for corrupt JSON session, "
            f"got: {redis_client.delete.call_count}"
        )


class TestRemoveSessionFromIndex:
    """Line 313-314: remove_session_from_index calls srem correctly."""

    def test_remove_session_calls_srem(self) -> None:
        """remove_session_from_index calls redis.srem with the correct index key.

        Lines 313-314: the function body — calls srem to remove a session key
        from the per-user session index.
        """
        from synth_engine.bootstrapper.dependencies.sessions import (
            remove_session_from_index,
        )

        redis_client = MagicMock()
        session_key = "conclave:session:some-token"

        remove_session_from_index(
            redis_client=redis_client,
            user_id="user-abc",
            org_id="org-xyz",
            session_key=session_key,
        )

        redis_client.srem.assert_called_once()
        call_args = redis_client.srem.call_args[0]
        # First arg is the index key, second is the session key
        assert "user-abc" in call_args[0], f"Index key must contain user_id, got: {call_args[0]}"
        assert "org-xyz" in call_args[0], f"Index key must contain org_id, got: {call_args[0]}"
        assert call_args[1] == session_key, (
            f"srem must be called with the session_key, got: {call_args[1]}"
        )


# ===========================================================================
# lifecycle.py — error paths
# ===========================================================================


class TestLifecyclePrometheusShutdown:
    """Lines 89-90: mark_process_dead error path in _lifespan shutdown."""

    def test_mark_process_dead_oserror_does_not_propagate(self) -> None:
        """OSError from mark_process_dead during shutdown must be caught and logged.

        Lines 89-90: the `except (OSError, ValueError)` clause around mark_process_dead.
        When PROMETHEUS_MULTIPROC_DIR is set, mark_process_dead is called; if it
        raises OSError, the warning is logged and shutdown continues cleanly.
        """
        import asyncio

        from fastapi import FastAPI

        from synth_engine.bootstrapper.lifecycle import _lifespan

        app = FastAPI()

        # Track that mark_process_dead was attempted (and its OSError was swallowed)
        mock_mark_process_dead = MagicMock(side_effect=OSError("prometheus file error"))

        async def _run_lifespan_with_tracking() -> None:
            with patch("synth_engine.bootstrapper.lifecycle.validate_config"):
                with patch("synth_engine.bootstrapper.lifecycle.update_cert_expiry_metrics"):
                    with patch(
                        "synth_engine.bootstrapper.lifecycle.maybe_initialize_oidc_provider"
                    ):
                        with patch("synth_engine.bootstrapper.lifecycle.dispose_engines"):
                            with patch("synth_engine.bootstrapper.lifecycle.close_redis_client"):
                                with patch(
                                    "synth_engine.bootstrapper.lifecycle.get_audit_logger"
                                ) as mock_audit:
                                    mock_audit.return_value.log_event = MagicMock()
                                    with patch(
                                        "synth_engine.bootstrapper.lifecycle.mark_process_dead",
                                        mock_mark_process_dead,
                                    ):
                                        with patch.dict(
                                            os.environ,
                                            {"PROMETHEUS_MULTIPROC_DIR": "/tmp/prometheus"},
                                        ):
                                            async with _lifespan(app):
                                                pass

        asyncio.run(_run_lifespan_with_tracking())
        # Lifespan completed — OSError from mark_process_dead was caught and swallowed.
        # Verify mark_process_dead was called (PROMETHEUS_MULTIPROC_DIR branch was taken).
        assert mock_mark_process_dead.call_count == 1, (
            f"Expected mark_process_dead called once, got: {mock_mark_process_dead.call_count}"
        )


class TestLifecycleVaultUnsealAuditSkip:
    """Lines 138-139: audit log skip path when AUDIT_KEY is not configured."""

    def test_unseal_succeeds_when_audit_key_raises_value_error(self) -> None:
        """unseal_vault returns 200 even when audit logging raises ValueError.

        Lines 138-139: the `except (ValueError, OSError, UnicodeError)` clause
        around get_audit_logger().log_event() in unseal_vault. When AUDIT_KEY
        is unconfigured, log_event raises ValueError — the response must still
        return the 200 success JSON.
        """
        import asyncio

        from fastapi import FastAPI

        from synth_engine.bootstrapper.lifecycle import _register_routes
        from synth_engine.bootstrapper.schemas.vault import UnsealRequest

        app = FastAPI()
        _register_routes(app)

        from fastapi.routing import APIRoute

        # Extract the unseal_vault handler from the registered routes
        api_route = next(r for r in app.routes if getattr(r, "path", None) == "/unseal")
        assert isinstance(api_route, APIRoute), f"Expected APIRoute, got {type(api_route)}"
        endpoint = api_route.endpoint

        body = UnsealRequest(passphrase="correct-passphrase")

        async def _run() -> Any:
            with patch("synth_engine.bootstrapper.lifecycle.VaultState.unseal"):
                with patch("synth_engine.bootstrapper.lifecycle.get_audit_logger") as mock_audit:
                    # Simulate unconfigured AUDIT_KEY — log_event raises ValueError
                    mock_audit.return_value.log_event.side_effect = ValueError(
                        "AUDIT_KEY not configured"
                    )
                    return await endpoint(body)

        response = asyncio.run(_run())
        assert response.status_code == 200, (
            f"Expected 200 when audit log raises ValueError, got: {response.status_code}"
        )
        body_content = json.loads(response.body.decode("utf-8"))
        assert body_content.get("status") == "unsealed", (
            f"Expected 'unsealed' in response body, got: {body_content}"
        )


# ===========================================================================
# modules/synthesizer/storage/retention.py — artifact sweep exception paths
# ===========================================================================


def _make_retention_engine() -> Any:
    """Create an in-memory SQLite engine with SynthesisJob schema."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return engine


def _backdate_job(session: Session, job_id: int, days: int) -> None:
    """Backdate a job's created_at by ``days`` days."""
    from sqlalchemy import text

    cutoff = datetime.now(UTC) - timedelta(days=days)
    session.exec(  # type: ignore[call-overload]
        text("UPDATE synthesis_job SET created_at = :ts WHERE id = :id").bindparams(
            ts=cutoff.isoformat(), id=job_id
        )
    )
    session.commit()


class TestRetentionArtifactSweepExceptionPaths:
    """Lines 228-239, 260-261: exception handling in cleanup_expired_artifacts."""

    def test_sqlalchemy_error_in_artifact_sweep_does_not_raise(self) -> None:
        """SQLAlchemyError during artifact commit is caught and loop continues.

        Lines 228-239: the `except (OSError, SQLAlchemyError)` clause in
        cleanup_expired_artifacts. When session.commit() raises SQLAlchemyError,
        the warning is logged, session is rolled back, and the loop continues
        to the next job (does not abort).
        """
        from synth_engine.modules.synthesizer.storage.retention import RetentionCleanup

        engine = _make_retention_engine()

        with Session(engine) as session:
            job = SynthesisJob(
                table_name="t",
                parquet_path="/tmp/artifact.parquet",
                output_path="/tmp/artifact.parquet",
                total_epochs=1,
                num_rows=1,
                status="COMPLETE",
            )
            session.add(job)
            session.commit()
            job_id = job.id
            assert job_id is not None
            _backdate_job(session, job_id, days=100)

        cleanup = RetentionCleanup(
            engine=engine,
            job_retention_days=90,
            artifact_retention_days=90,
        )

        # Patch session.commit to raise SQLAlchemyError on the first call
        with patch(
            "synth_engine.modules.synthesizer.storage.retention.Session"
        ) as mock_session_cls:
            mock_session_instance = MagicMock()
            mock_session_cls.return_value.__enter__ = MagicMock(return_value=mock_session_instance)
            mock_session_cls.return_value.__exit__ = MagicMock(return_value=False)

            # exec returns a mock that produces one job
            mock_exec_result = MagicMock()
            mock_exec_result.all.return_value = [
                SynthesisJob(
                    id=1,
                    table_name="t",
                    output_path="/tmp/artifact.parquet",
                    status="COMPLETE",
                    total_epochs=1,
                    num_rows=1,
                )
            ]
            mock_session_instance.exec.return_value = mock_exec_result
            mock_session_instance.commit.side_effect = SQLAlchemyError("deadlock detected")

            # Must not raise — SQLAlchemyError is caught and loop continues
            result = cleanup.cleanup_expired_artifacts()

        assert result == 0, (
            f"Expected 0 swept artifacts when commit raises SQLAlchemyError, got: {result}"
        )

    def test_audit_log_failure_in_artifact_sweep_does_not_raise(self) -> None:
        """Audit log failure after successful artifact sweep is caught and logged.

        Lines 260-261: the broad `except Exception` clause around get_audit_logger()
        inside cleanup_expired_artifacts. When audit logging raises any exception,
        the warning is logged but the artifact is still counted as swept.
        """
        from synth_engine.modules.synthesizer.storage.retention import RetentionCleanup

        engine = _make_retention_engine()

        with Session(engine) as session:
            job = SynthesisJob(
                table_name="t",
                parquet_path="/tmp/artifact2.parquet",
                output_path="/tmp/artifact2.parquet",
                total_epochs=1,
                num_rows=1,
                status="COMPLETE",
            )
            session.add(job)
            session.commit()
            job_id = job.id
            assert job_id is not None
            _backdate_job(session, job_id, days=100)

        cleanup = RetentionCleanup(
            engine=engine,
            job_retention_days=90,
            artifact_retention_days=90,
        )

        with patch(
            "synth_engine.modules.synthesizer.storage.retention.get_audit_logger"
        ) as mock_audit:
            mock_audit.return_value.log_event.side_effect = RuntimeError(
                "audit service unavailable"
            )
            # Must not raise — audit failure is best-effort
            result = cleanup.cleanup_expired_artifacts()

        # The artifact was swept (output_path cleared) even though audit logging failed
        assert result >= 1, (
            f"Expected at least 1 swept artifact despite audit log failure, got: {result}"
        )
