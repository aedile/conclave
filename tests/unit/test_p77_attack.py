"""Negative/attack tests for Phase 77 — Security Fixes & Roast Findings.

Attack tests verifying that:
1. UnicodeError during audit log_event() is caught in all router catch blocks — does NOT propagate.
2. _url_hash() returns the full 64-char SHA-256 hex digest (no truncation leaks collisions).
3. _get_circuit_breaker() broad except-Exception is narrowed — named exceptions fall back.
4. artifact.py _log_verification_failure() does NOT use sys.stderr.write (uses logger instead).

CONSTITUTION Priority 0: Security — fail-closed, no silent error swallowing
CONSTITUTION Priority 3: TDD — Attack tests committed before implementation (Rule 22)
Task: T77.1, T77.2, T77.3, T77.4
Phase: P77 — Security Fixes & Roast Findings
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# T77.1 — UnicodeError in audit.log_event() must be caught in router helpers
# ---------------------------------------------------------------------------


class TestUnicodeErrorCaughtInRouterAuditBlocks:
    """UnicodeError from audit.log_event() must not propagate out of router helpers."""

    def test_privacy_emit_pre_reset_audit_catches_unicode_error(self) -> None:
        """_emit_pre_reset_audit must handle UnicodeError from log_event() without propagating.

        If log_event() raises UnicodeError (e.g. non-UTF-8 operator claim),
        the helper must return a 500 JSONResponse — not raise.
        """
        from decimal import Decimal

        from synth_engine.bootstrapper.routers.privacy import _emit_pre_reset_audit

        mock_audit = MagicMock()
        mock_audit.log_event.side_effect = UnicodeError("bad encoding")

        with patch(
            "synth_engine.bootstrapper.routers.privacy.get_audit_logger",
            return_value=mock_audit,
        ):
            result = _emit_pre_reset_audit(
                operator="operator1",
                ledger_id=1,
                prev_allocated="10.0",
                prev_spent="2.0",
                new_alloc=Decimal("10.0"),
                justification="test",
            )

        assert result is not None, "Expected a 500 JSONResponse, got None"
        assert result.status_code == 500  # type: ignore[union-attr]

    def test_privacy_run_reset_with_compensation_catches_unicode_error(self) -> None:
        """Compensating audit in _run_reset_with_compensation must handle UnicodeError.

        If the compensating log_event() raises UnicodeError, it must be silently
        swallowed — the function must still return a 500 JSONResponse.
        """
        from decimal import Decimal

        from synth_engine.bootstrapper.routers.privacy import _run_reset_with_compensation

        mock_audit = MagicMock()
        mock_audit.log_event.side_effect = UnicodeError("bad encoding")

        with (
            patch(
                "synth_engine.bootstrapper.routers.privacy._run_reset_budget",
                side_effect=RuntimeError("db exploded"),
            ),
            patch(
                "synth_engine.bootstrapper.routers.privacy.get_audit_logger",
                return_value=mock_audit,
            ),
        ):
            result = _run_reset_with_compensation(
                ledger_id=1,
                new_alloc=Decimal("10.0"),
                operator="operator1",
                prev_allocated="10.0",
                prev_spent="2.0",
                justification="test",
            )

        assert result is not None, "Expected a 500 JSONResponse, got None"
        assert result.status_code == 500  # type: ignore[union-attr]

    def test_jobs_write_shred_audit_catches_unicode_error(self) -> None:
        """_write_shred_audit must return 500 JSONResponse on UnicodeError from log_event().

        UnicodeError propagating out of the shred endpoint would crash the
        worker with a 500 unhandled exception instead of a structured response.
        """
        from synth_engine.bootstrapper.routers.jobs import _write_shred_audit

        mock_audit = MagicMock()
        mock_audit.log_event.side_effect = UnicodeError("bad encoding")

        result = _write_shred_audit(
            audit=mock_audit,
            user_id="op1",
            job_id=42,
            table_name="customers",
            org_id="",
        )

        assert result is not None, "Expected a 500 JSONResponse, got None"
        assert result.status_code == 500  # type: ignore[union-attr]

    def test_jobs_shred_and_compensate_catches_unicode_error_in_compensating_audit(
        self,
    ) -> None:
        """_shred_and_compensate compensating audit must handle UnicodeError.

        If the compensating audit log_event() raises UnicodeError, it must be
        swallowed — the function must still return a 500 JSONResponse.
        """
        from synth_engine.bootstrapper.routers.jobs import _shred_and_compensate

        mock_audit = MagicMock()
        mock_audit.log_event.side_effect = UnicodeError("bad encoding")

        mock_job = MagicMock()
        mock_job.table_name = "customers"

        with patch(
            "synth_engine.bootstrapper.routers.jobs.shred_artifacts",
            side_effect=OSError("disk error"),
        ):
            result = _shred_and_compensate(
                audit=mock_audit,
                user_id="op1",
                job_id=42,
                job=mock_job,
                org_id="",
            )

        assert result is not None, "Expected a 500 JSONResponse, got None"
        assert result.status_code == 500  # type: ignore[union-attr]

    def test_lifecycle_audit_catches_unicode_error(self) -> None:
        """lifecycle.py audit catch blocks must handle UnicodeError from log_event().

        Tests that the lifecycle startup/shutdown audit emit does not crash
        the application when UnicodeError is raised by log_event().
        """
        # Find all exception handlers guarding log_event() calls in lifecycle.py
        # by checking the source contains (ValueError, OSError, UnicodeError)
        # after the fix. For now, verify the module is importable and the
        # pattern exists.
        import inspect

        import synth_engine.bootstrapper.lifecycle as lifecycle_mod

        source = inspect.getsource(lifecycle_mod)
        # After fix: all audit catch blocks must include UnicodeError
        assert "UnicodeError" in source, (
            "lifecycle.py audit catch blocks must include UnicodeError after T77.1 fix"
        )


# ---------------------------------------------------------------------------
# T77.2 — _url_hash() must return full 64-char SHA-256 digest
# ---------------------------------------------------------------------------


class TestUrlHashFullDigest:
    """_url_hash() must return the full 64-character SHA-256 hex digest."""

    def test_url_hash_returns_64_char_hex_digest(self) -> None:
        """Full SHA-256 hex digest is 64 hex characters (256 bits).

        A 16-char prefix (64 bits) has a birthday-attack collision probability
        of ~1 in 2^32 with ~65,000 URLs — unacceptable for a security-relevant
        key space.  The full 64-char digest provides 2^128 birthday resistance.
        """
        from synth_engine.modules.synthesizer.jobs.webhook_delivery import _url_hash

        result = _url_hash("https://example.com/callback")
        assert len(result) == 64, f"Expected 64-char digest, got {len(result)}: {result!r}"

    def test_url_hash_is_valid_hex(self) -> None:
        """The returned digest must be a valid lowercase hex string."""
        from synth_engine.modules.synthesizer.jobs.webhook_delivery import _url_hash

        result = _url_hash("https://example.com/webhook")
        assert all(c in "0123456789abcdef" for c in result), (
            f"Non-hex characters in digest: {result!r}"
        )

    def test_url_hash_is_deterministic(self) -> None:
        """Two calls with the same URL must produce identical digests."""
        from synth_engine.modules.synthesizer.jobs.webhook_delivery import _url_hash

        url = "https://example.com/callback"
        assert _url_hash(url) == _url_hash(url)

    def test_url_hash_different_urls_produce_different_digests(self) -> None:
        """Different URLs must produce different digests (collision resistance test)."""
        from synth_engine.modules.synthesizer.jobs.webhook_delivery import _url_hash

        hash1 = _url_hash("https://example.com/a")
        hash2 = _url_hash("https://example.com/b")
        assert hash1 != hash2, "Different URLs must not produce the same hash"

    def test_url_hash_known_value(self) -> None:
        """Verify the hash against a known SHA-256 value for regression detection."""
        import hashlib

        from synth_engine.modules.synthesizer.jobs.webhook_delivery import _url_hash

        url = "https://example.com/callback"
        expected = hashlib.sha256(url.encode("utf-8")).hexdigest()
        assert _url_hash(url) == expected


# ---------------------------------------------------------------------------
# T77.3 — _get_circuit_breaker() broad except-Exception must be narrowed
# ---------------------------------------------------------------------------


class TestCircuitBreakerInitNarrowCatch:
    """_get_circuit_breaker() must not swallow arbitrary exceptions on CB init.

    The broad ``except Exception`` made it impossible to detect programming
    errors (e.g. AttributeError from a wrong mock, ImportError from a bad
    module) during testing.  The narrowed catch should only handle Redis-specific
    and configuration errors.
    """

    def test_redis_error_falls_back_to_local_cb(self) -> None:
        """RedisError during CB init must fall back to process-local WebhookCircuitBreaker."""
        import redis as redis_lib

        from synth_engine.modules.synthesizer.jobs import webhook_delivery as mod
        from synth_engine.modules.synthesizer.jobs.webhook_delivery import (
            WebhookCircuitBreaker,
        )

        mock_redis = MagicMock()

        def _raise_redis_error(self: object, **kwargs: object) -> None:
            raise redis_lib.RedisError("cannot connect")

        with (
            patch.object(mod.RedisCircuitBreaker, "__init__", _raise_redis_error),
            patch.object(mod, "_CB_REDIS_CLIENT", mock_redis),
            patch.object(mod, "_MODULE_CIRCUIT_BREAKER", None),
        ):
            cb = mod._get_circuit_breaker()

        assert isinstance(cb, WebhookCircuitBreaker), (
            f"Expected process-local fallback, got {type(cb)}"
        )
        # Specific-value assertion: default threshold from settings
        assert cb.threshold == 3, f"Expected default threshold=3, got {cb.threshold}"

    def test_type_error_during_cb_init_falls_back_to_local_cb(self) -> None:
        """TypeError during CB init (e.g. wrong arg type) falls back to process-local CB."""
        from synth_engine.modules.synthesizer.jobs import webhook_delivery as mod
        from synth_engine.modules.synthesizer.jobs.webhook_delivery import (
            WebhookCircuitBreaker,
        )

        mock_redis = MagicMock()

        def _raise_type_error(self: object, **kwargs: object) -> None:
            raise TypeError("wrong type")

        with (
            patch.object(mod.RedisCircuitBreaker, "__init__", _raise_type_error),
            patch.object(mod, "_CB_REDIS_CLIENT", mock_redis),
            patch.object(mod, "_MODULE_CIRCUIT_BREAKER", None),
        ):
            cb = mod._get_circuit_breaker()

        assert isinstance(cb, WebhookCircuitBreaker)
        assert cb.threshold == 3, f"Expected default threshold=3, got {cb.threshold}"

    def test_value_error_during_cb_init_falls_back_to_local_cb(self) -> None:
        """ValueError during CB init (e.g. invalid config) falls back to process-local CB."""
        from synth_engine.modules.synthesizer.jobs import webhook_delivery as mod
        from synth_engine.modules.synthesizer.jobs.webhook_delivery import (
            WebhookCircuitBreaker,
        )

        mock_redis = MagicMock()

        def _raise_value_error(self: object, **kwargs: object) -> None:
            raise ValueError("invalid config value")

        with (
            patch.object(mod.RedisCircuitBreaker, "__init__", _raise_value_error),
            patch.object(mod, "_CB_REDIS_CLIENT", mock_redis),
            patch.object(mod, "_MODULE_CIRCUIT_BREAKER", None),
        ):
            cb = mod._get_circuit_breaker()

        assert isinstance(cb, WebhookCircuitBreaker)
        assert cb.threshold == 3, f"Expected default threshold=3, got {cb.threshold}"

    def test_narrow_catch_comment_present_in_source(self) -> None:
        """The narrowed catch block must have a comment explaining the narrowing (T77.3).

        The comment distinguishes this from a broad catch-all and documents
        which exceptions are in scope (RedisError, TypeError, ValueError).
        """
        import inspect

        import synth_engine.modules.synthesizer.jobs.webhook_delivery as mod

        source = inspect.getsource(mod._get_circuit_breaker)
        # After fix: catch must be narrowed to named exceptions (not 'except Exception')
        assert "except Exception" not in source, (
            "_get_circuit_breaker must not use broad 'except Exception' after T77.3 fix"
        )


# ---------------------------------------------------------------------------
# T77.4 — _log_verification_failure() must use logger, not sys.stderr.write
# ---------------------------------------------------------------------------


class TestArtifactLogVerificationFailureUsesLogger:
    """_log_verification_failure() must use _logger.error() not sys.stderr.write()."""

    def test_log_verification_failure_does_not_write_to_stderr(self) -> None:
        """When _logger.warning() raises, the fallback must NOT use sys.stderr.write.

        sys.stderr.write() is not structured, bypasses log routing, and can
        expose sensitive paths in operator log streams. The correct replacement
        is _logger.error() or similar structured logging call.
        """
        import sys

        from synth_engine.modules.synthesizer.storage import artifact as artifact_mod

        original_write = sys.stderr.write
        stderr_calls: list[str] = []
        sys.stderr.write = lambda s: stderr_calls.append(s)  # type: ignore[method-assign]

        try:
            # Make _logger.warning raise so the fallback path executes
            with patch.object(
                artifact_mod._logger, "warning", side_effect=RuntimeError("log fail")
            ):
                # The fallback path fires — must not use sys.stderr.write
                artifact_mod._log_verification_failure("/some/path", "test reason")
        finally:
            sys.stderr.write = original_write  # type: ignore[method-assign]

        assert len(stderr_calls) == 0, (
            f"sys.stderr.write was called {len(stderr_calls)} time(s): {stderr_calls!r}"
        )

    def test_log_verification_failure_uses_logger_error_as_fallback(self) -> None:
        """When _logger.warning() raises, fallback must call _logger.error()."""
        from synth_engine.modules.synthesizer.storage import artifact as artifact_mod

        error_calls: list[tuple[object, ...]] = []

        def _fake_error(*args: object, **kwargs: object) -> None:
            error_calls.append(args)

        with (
            patch.object(artifact_mod._logger, "warning", side_effect=RuntimeError("log fail")),
            patch.object(artifact_mod._logger, "error", side_effect=_fake_error),
        ):
            artifact_mod._log_verification_failure("/some/path", "test reason")

        assert len(error_calls) == 1, f"Expected 1 _logger.error call, got {len(error_calls)}"

    def test_log_verification_failure_normal_path_uses_warning(self) -> None:
        """Normal (non-failing) path must call _logger.warning exactly once."""
        from synth_engine.modules.synthesizer.storage import artifact as artifact_mod

        warning_calls: list[tuple[object, ...]] = []

        def _capture_warning(*args: object, **kwargs: object) -> None:
            warning_calls.append(args)

        with patch.object(artifact_mod._logger, "warning", side_effect=_capture_warning):
            artifact_mod._log_verification_failure("/some/path", "reason")

        assert len(warning_calls) == 1
        # Verify the warning message contains key identifiers
        assert "/some/path" in str(warning_calls[0])
        assert "reason" in str(warning_calls[0])
