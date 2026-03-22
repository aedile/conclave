"""Negative/attack and feature unit tests for shutdown cleanup (T47.8).

Covers:
- Shutdown cleanup lifecycle: dispose_engines, close_redis_client, audit logging
- Idempotency and partial-failure resilience of shutdown
- TLS 1.3 minimum version pin in _build_asyncpg_ssl_context()
- close_redis_client() reset behaviour

CONSTITUTION Priority 3: TDD RED Phase — attack tests first, per Rule 22.
Task: T47.8 — Add Shutdown Cleanup to Lifespan Hook + ADV-P46-01 TLS 1.3 Pin
"""

from __future__ import annotations

import ssl
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Negative / attack tests (committed first per Rule 22)
# ---------------------------------------------------------------------------


def test_redis_never_initialized_shutdown_noop() -> None:
    """close_redis_client() must be a no-op when _client was never initialized.

    Attack vector: Calling close before Redis is ever used must not raise.
    """
    import synth_engine.bootstrapper.dependencies.redis as redis_mod

    original = redis_mod._client
    try:
        redis_mod._client = None
        # Must not raise
        from synth_engine.bootstrapper.dependencies.redis import close_redis_client

        close_redis_client()
    finally:
        redis_mod._client = original


def test_shutdown_audit_failure_does_not_block() -> None:
    """Audit log failure during shutdown must not prevent cleanup from continuing.

    Attack vector: If get_audit_logger() raises, dispose_engines and
    close_redis_client must still be called.
    """
    with (
        patch(
            "synth_engine.bootstrapper.lifecycle.get_audit_logger",
            side_effect=RuntimeError("audit unavailable"),
        ),
        patch("synth_engine.bootstrapper.lifecycle.dispose_engines") as mock_dispose,
        patch(
            "synth_engine.bootstrapper.lifecycle.close_redis_client"
        ) as mock_redis_close,
    ):
        import asyncio

        from synth_engine.bootstrapper.lifecycle import _lifespan
        from fastapi import FastAPI

        app = FastAPI()

        async def run() -> None:
            async with _lifespan(app):
                pass

        asyncio.run(run())

        mock_dispose.assert_called_once()
        mock_redis_close.assert_called_once()


def test_dispose_engines_failure_does_not_skip_redis() -> None:
    """dispose_engines() raising must not prevent close_redis_client() from running.

    Attack vector: A partial failure in dispose_engines must not short-circuit
    the remaining cleanup steps.
    """
    with (
        patch(
            "synth_engine.bootstrapper.lifecycle.get_audit_logger",
        ) as mock_get_audit,
        patch(
            "synth_engine.bootstrapper.lifecycle.dispose_engines",
            side_effect=RuntimeError("db pool exploded"),
        ),
        patch(
            "synth_engine.bootstrapper.lifecycle.close_redis_client"
        ) as mock_redis_close,
    ):
        mock_get_audit.return_value = MagicMock()

        import asyncio

        from synth_engine.bootstrapper.lifecycle import _lifespan
        from fastapi import FastAPI

        app = FastAPI()

        async def run() -> None:
            async with _lifespan(app):
                pass

        asyncio.run(run())

        mock_redis_close.assert_called_once()


def test_shutdown_cleanup_idempotent() -> None:
    """Calling shutdown cleanup twice must not raise.

    Attack vector: Repeated SIGTERM or duplicate teardown calls must be safe.
    """
    with (
        patch(
            "synth_engine.bootstrapper.lifecycle.get_audit_logger",
        ) as mock_get_audit,
        patch("synth_engine.bootstrapper.lifecycle.dispose_engines"),
        patch("synth_engine.bootstrapper.lifecycle.close_redis_client"),
    ):
        mock_get_audit.return_value = MagicMock()

        import asyncio

        from synth_engine.bootstrapper.lifecycle import _lifespan
        from fastapi import FastAPI

        app = FastAPI()

        async def run() -> None:
            async with _lifespan(app):
                pass
            async with _lifespan(app):
                pass

        # Must not raise
        asyncio.run(run())


def test_close_redis_client_when_none_is_noop() -> None:
    """close_redis_client() with _client already None must not raise.

    Attack vector: Double-close must be safe (idempotency check).
    """
    import synth_engine.bootstrapper.dependencies.redis as redis_mod

    original = redis_mod._client
    try:
        redis_mod._client = None
        from synth_engine.bootstrapper.dependencies.redis import close_redis_client

        close_redis_client()
        close_redis_client()  # second call — still must not raise
    finally:
        redis_mod._client = original


# ---------------------------------------------------------------------------
# Feature tests
# ---------------------------------------------------------------------------


def test_shutdown_dispose_engines_called() -> None:
    """dispose_engines() must be called exactly once during shutdown cleanup."""
    with (
        patch(
            "synth_engine.bootstrapper.lifecycle.get_audit_logger",
        ) as mock_get_audit,
        patch("synth_engine.bootstrapper.lifecycle.dispose_engines") as mock_dispose,
        patch("synth_engine.bootstrapper.lifecycle.close_redis_client"),
    ):
        mock_get_audit.return_value = MagicMock()

        import asyncio

        from synth_engine.bootstrapper.lifecycle import _lifespan
        from fastapi import FastAPI

        app = FastAPI()

        async def run() -> None:
            async with _lifespan(app):
                pass

        asyncio.run(run())

        mock_dispose.assert_called_once()


def test_shutdown_redis_close_called() -> None:
    """close_redis_client() must be called exactly once during shutdown cleanup."""
    with (
        patch(
            "synth_engine.bootstrapper.lifecycle.get_audit_logger",
        ) as mock_get_audit,
        patch("synth_engine.bootstrapper.lifecycle.dispose_engines"),
        patch(
            "synth_engine.bootstrapper.lifecycle.close_redis_client"
        ) as mock_redis_close,
    ):
        mock_get_audit.return_value = MagicMock()

        import asyncio

        from synth_engine.bootstrapper.lifecycle import _lifespan
        from fastapi import FastAPI

        app = FastAPI()

        async def run() -> None:
            async with _lifespan(app):
                pass

        asyncio.run(run())

        mock_redis_close.assert_called_once()


def test_shutdown_audit_event_emitted() -> None:
    """Shutdown must emit a SERVER_SHUTDOWN audit event."""
    with (
        patch(
            "synth_engine.bootstrapper.lifecycle.get_audit_logger",
        ) as mock_get_audit,
        patch("synth_engine.bootstrapper.lifecycle.dispose_engines"),
        patch("synth_engine.bootstrapper.lifecycle.close_redis_client"),
    ):
        mock_audit = MagicMock()
        mock_get_audit.return_value = mock_audit

        import asyncio

        from synth_engine.bootstrapper.lifecycle import _lifespan
        from fastapi import FastAPI

        app = FastAPI()

        async def run() -> None:
            async with _lifespan(app):
                pass

        asyncio.run(run())

        mock_audit.log_event.assert_called_once()
        call_kwargs = mock_audit.log_event.call_args.kwargs
        assert call_kwargs["event_type"] == "SERVER_SHUTDOWN"


def test_close_redis_client_resets_to_none() -> None:
    """After close_redis_client(), the module _client must be None."""
    import synth_engine.bootstrapper.dependencies.redis as redis_mod

    original = redis_mod._client
    try:
        mock_redis = MagicMock()
        mock_redis.connection_pool = MagicMock()
        redis_mod._client = mock_redis

        from synth_engine.bootstrapper.dependencies.redis import close_redis_client

        close_redis_client()

        assert redis_mod._client is None
    finally:
        redis_mod._client = original


def test_close_redis_client_disconnects_pool() -> None:
    """close_redis_client() must call disconnect() on the connection pool."""
    import synth_engine.bootstrapper.dependencies.redis as redis_mod

    original = redis_mod._client
    try:
        mock_pool = MagicMock()
        mock_redis = MagicMock()
        mock_redis.connection_pool = mock_pool
        redis_mod._client = mock_redis

        from synth_engine.bootstrapper.dependencies.redis import close_redis_client

        close_redis_client()

        mock_pool.disconnect.assert_called_once()
    finally:
        redis_mod._client = original


def test_tls_minimum_version_is_tls_1_3() -> None:
    """_build_asyncpg_ssl_context() must set minimum_version to TLSv1.3.

    ADV-P46-01: Pinning TLS 1.3 ensures legacy TLS is rejected even if
    OpenSSL defaults change.
    """
    with patch(
        "synth_engine.shared.db.get_settings",
    ) as mock_settings:
        settings = MagicMock()
        settings.mtls_ca_cert_path = "/dev/null"
        settings.mtls_client_cert_path = "/dev/null"
        settings.mtls_client_key_path = "/dev/null"
        mock_settings.return_value = settings

        with (
            patch("ssl.SSLContext.load_verify_locations"),
            patch("ssl.SSLContext.load_cert_chain"),
        ):
            from synth_engine.shared.db import _build_asyncpg_ssl_context

            ctx = _build_asyncpg_ssl_context()

            assert ctx.minimum_version == ssl.TLSVersion.TLSv1_3
