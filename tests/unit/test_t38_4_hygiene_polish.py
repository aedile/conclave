"""Hygiene polish validation tests for T38.4.

Tests verifying:
1. request_limits.py: The narrowed ValueError only catches Content-Length
   parse failures. A ValueError raised OUTSIDE the parsing line (e.g. from
   the comparison) would propagate uncaught — tested by patching.
2. job_finalization.py: The signing key parse failure logs at ERROR level,
   not WARNING.

CONSTITUTION Priority 3: TDD (RED phase)
Task: T38.4 — Documentation & Hygiene Polish Batch
"""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers shared across tests
# ---------------------------------------------------------------------------


def _make_http_scope(
    content_length_raw: bytes | None = None,
    content_type: str = "application/json",
) -> dict[str, Any]:
    """Build a minimal ASGI HTTP scope.

    Args:
        content_length_raw: Raw bytes value for Content-Length header, or None.
        content_type: Value for the Content-Type header.

    Returns:
        A dict suitable as an ASGI scope for HTTP requests.
    """
    headers: list[tuple[bytes, bytes]] = [
        (b"content-type", content_type.encode()),
    ]
    if content_length_raw is not None:
        headers.append((b"content-length", content_length_raw))
    return {
        "type": "http",
        "method": "POST",
        "path": "/test",
        "headers": headers,
    }


def _make_receive(body: bytes = b"{}") -> Any:
    """Build a minimal ASGI receive callable returning one http.request.

    Args:
        body: Body bytes to return.

    Returns:
        An async receive callable.
    """
    called = [False]

    async def _receive() -> dict[str, Any]:
        if not called[0]:
            called[0] = True
            return {"type": "http.request", "body": body, "more_body": False}
        return {"type": "http.disconnect"}

    return _receive


def _make_send() -> tuple[Any, list[dict[str, Any]]]:
    """Build an ASGI send callable that records messages.

    Returns:
        Tuple of (send callable, messages list).
    """
    messages: list[dict[str, Any]] = []

    async def _send(msg: dict[str, Any]) -> None:
        messages.append(msg)

    return _send, messages


# ---------------------------------------------------------------------------
# T38.4 item 3: request_limits.py — narrowed ValueError try/except
# ---------------------------------------------------------------------------


class TestNarrowedValueErrorInRequestLimits:
    """Verify the ValueError try/except is narrowed to the parsing line only.

    The narrowed structure must:
    - Still catch ValueError from int(content_length_raw) when the header
      is non-numeric (existing behavior preserved).
    - NOT silently swallow a ValueError that originates from code OUTSIDE
      the int() call (e.g., from a comparison or other logic).

    Since the comparison `int(...) > MAX_BODY_BYTES` cannot itself raise
    ValueError, we test the narrow scope indirectly: we verify the try/except
    is structured so that only the int() call is inside it by confirming the
    int() ValueError is still caught and logged as a WARNING.
    """

    @pytest.mark.asyncio
    async def test_non_integer_content_length_still_caught_as_warning(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Non-integer Content-Length header still produces a WARNING.

        Verifies that narrowing the try/except does not break the existing
        warning behavior for malformed Content-Length headers.
        """
        from synth_engine.bootstrapper.dependencies.request_limits import (
            RequestBodyLimitMiddleware,
        )

        inner = AsyncMock()
        middleware = RequestBodyLimitMiddleware(inner)

        scope = _make_http_scope(content_length_raw=b"not-a-number")
        receive = _make_receive()
        send, _ = _make_send()

        with caplog.at_level(logging.WARNING):
            await middleware(scope, receive, send)

        # The ValueError from int("not-a-number") must be caught and logged.
        warning_messages = [r.message for r in caplog.records if r.levelno == logging.WARNING]
        assert any("Non-integer Content-Length" in msg for msg in warning_messages), (
            "Expected WARNING about non-integer Content-Length header"
        )

        # Inner app must proceed (warning, not rejection)
        inner.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_integer_content_length_within_limit_passes(self) -> None:
        """Valid integer Content-Length within limit passes through normally."""
        from synth_engine.bootstrapper.dependencies.request_limits import (
            MAX_BODY_BYTES,
            RequestBodyLimitMiddleware,
        )

        inner = AsyncMock()
        middleware = RequestBodyLimitMiddleware(inner)

        # Use a Content-Length that is within the limit
        scope = _make_http_scope(content_length_raw=str(MAX_BODY_BYTES - 100).encode())
        receive = _make_receive()
        send, messages = _make_send()

        await middleware(scope, receive, send)

        # Must NOT be rejected — inner app should be called
        inner.assert_awaited_once()
        assert not any(m.get("status") == 413 for m in messages)

    @pytest.mark.asyncio
    async def test_try_except_does_not_catch_valueerror_from_comparison(
        self,
    ) -> None:
        """ValueError from outside the int() parsing line must NOT be caught.

        This test verifies the structural correctness of the narrowed try/except.
        We inject a patched MAX_BODY_BYTES that raises a TypeError when compared,
        but we cannot inject ValueError there easily — instead we verify the
        narrow scope by confirming the int() parse is the only guarded call.

        Structural proof: with the narrow try/except, only the int() call is
        inside the try block. We test this by providing a valid integer Content-
        Length but patching MAX_BODY_BYTES to a value that would cause the
        comparison to behave abnormally — if the except was broad, it would
        catch; if narrow, it would not.

        This test serves as a regression guard: after narrowing, a ValueError
        raised by a hypothetical bug in the comparison would propagate to the
        caller rather than being silently swallowed.
        """
        from synth_engine.bootstrapper.dependencies import request_limits

        # Patch MAX_BODY_BYTES to raise ValueError when compared via int.__gt__
        # We do this by replacing the module-level constant with a mock that
        # raises ValueError on comparison. This is intentionally adversarial.

        class _RaisesOnCompare(int):
            def __gt__(self, other: object) -> bool:
                raise ValueError("injected from comparison, not from int()")

        original = request_limits.MAX_BODY_BYTES
        # Patch at the module level so the middleware uses our fake value
        request_limits.MAX_BODY_BYTES = _RaisesOnCompare(10)  # type: ignore[assignment]
        try:
            inner = AsyncMock()
            middleware = request_limits.RequestBodyLimitMiddleware(inner)

            scope = _make_http_scope(content_length_raw=b"5")
            receive = _make_receive()
            send, _ = _make_send()

            # With narrow try/except, the ValueError from comparison propagates.
            # With broad try/except, it would be silently swallowed (and logged
            # as a warning about a "Non-integer Content-Length").
            with pytest.raises(ValueError, match="injected from comparison"):
                await middleware(scope, receive, send)
        finally:
            request_limits.MAX_BODY_BYTES = original


# ---------------------------------------------------------------------------
# T38.4 item 4: job_finalization.py — signing key failure logs at ERROR
# ---------------------------------------------------------------------------


class TestJobFinalizationSigningKeyErrorLog:
    """Verify that a malformed ARTIFACT_SIGNING_KEY logs at ERROR, not WARNING."""

    def test_malformed_signing_key_logs_at_error_level(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """bytes.fromhex() failure on ARTIFACT_SIGNING_KEY must log at ERROR.

        A malformed signing key means the artifact will be written unsigned
        even though the operator intended to sign it. This is a security-
        relevant misconfiguration and warrants ERROR, not WARNING.
        """
        import pandas as pd

        from synth_engine.modules.synthesizer.job_finalization import (
            _write_parquet_with_signing,
        )

        df = pd.DataFrame({"id": [1, 2, 3], "name": ["a", "b", "c"]})

        with tempfile.TemporaryDirectory() as tmpdir:
            parquet_path = str(Path(tmpdir) / "test.parquet")

            # Patch settings to return a non-empty, invalid hex value
            with patch(
                "synth_engine.modules.synthesizer.job_finalization.get_settings"
            ) as mock_get_settings:
                mock_settings = mock_get_settings.return_value
                # "GGGG" is not valid hex — bytes.fromhex() raises ValueError
                mock_settings.artifact_signing_key = "GGGG"

                with caplog.at_level(logging.DEBUG):
                    _write_parquet_with_signing(df, parquet_path)

        # Must log at ERROR level (not WARNING) for malformed signing key
        error_records = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert len(error_records) >= 1, (
            f"Expected at least one ERROR log record for malformed signing key, "
            f"got: {[(r.levelno, r.message) for r in caplog.records]}"
        )
        assert any(
            "not valid hex" in r.message or "ARTIFACT_SIGNING_KEY" in r.message
            for r in error_records
        ), f"Expected ERROR message about invalid hex signing key, got: {[r.message for r in error_records]}"

    def test_malformed_signing_key_does_not_log_at_warning(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """bytes.fromhex() failure must NOT produce a WARNING for signing key.

        After T38.4, the log level is elevated to ERROR. A WARNING record for
        the invalid-hex signing key path is a regression.
        """
        import pandas as pd

        from synth_engine.modules.synthesizer.job_finalization import (
            _write_parquet_with_signing,
        )

        df = pd.DataFrame({"id": [1, 2, 3]})

        with tempfile.TemporaryDirectory() as tmpdir:
            parquet_path = str(Path(tmpdir) / "test.parquet")

            with patch(
                "synth_engine.modules.synthesizer.job_finalization.get_settings"
            ) as mock_get_settings:
                mock_settings = mock_get_settings.return_value
                mock_settings.artifact_signing_key = "GGGG"

                with caplog.at_level(logging.DEBUG):
                    _write_parquet_with_signing(df, parquet_path)

        # Must NOT produce a WARNING-level record for the signing key failure
        warning_records = [
            r
            for r in caplog.records
            if r.levelno == logging.WARNING
            and ("not valid hex" in r.message or "ARTIFACT_SIGNING_KEY" in r.message)
        ]
        assert len(warning_records) == 0, (
            f"Expected no WARNING for signing key failure after T38.4, "
            f"but got: {[r.message for r in warning_records]}"
        )

    def test_absent_signing_key_still_warns_not_errors(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Absent ARTIFACT_SIGNING_KEY still logs at WARNING (unsigned is OK in dev).

        This test ensures we did NOT accidentally elevate the absent-key warning
        to ERROR — only the malformed-hex path should be ERROR.
        """
        import pandas as pd

        from synth_engine.modules.synthesizer.job_finalization import (
            _write_parquet_with_signing,
        )

        df = pd.DataFrame({"id": [1, 2, 3]})

        with tempfile.TemporaryDirectory() as tmpdir:
            parquet_path = str(Path(tmpdir) / "test.parquet")

            with patch(
                "synth_engine.modules.synthesizer.job_finalization.get_settings"
            ) as mock_get_settings:
                mock_settings = mock_get_settings.return_value
                mock_settings.artifact_signing_key = ""  # empty = absent

                with caplog.at_level(logging.DEBUG):
                    _write_parquet_with_signing(df, parquet_path)

        # Absent key should log WARNING, not ERROR
        warning_records = [
            r
            for r in caplog.records
            if r.levelno == logging.WARNING and "unsigned" in r.message.lower()
        ]
        assert len(warning_records) >= 1, (
            "Expected a WARNING for absent signing key (unsigned artifact acceptable in dev)"
        )
