"""FastAPI lifespan hook and /unseal route.

``UnsealRequest`` re-exported from :mod:`schemas.vault` (T60.5).
``GET /health`` moved to :mod:`routers.health` (T60.2).
Shutdown catches narrowed from broad Exception to specific types (T72.2).
Tasks: P29-T29.3, T46.3, T47.8, T60.2, T60.5, T72.2
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from collections.abc import AsyncGenerator

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from prometheus_client.multiprocess import mark_process_dead as mark_process_dead
from redis.exceptions import RedisError
from sqlalchemy.exc import SQLAlchemyError

from synth_engine.bootstrapper.config_validation import validate_config
from synth_engine.bootstrapper.dependencies.redis import close_redis_client
from synth_engine.bootstrapper.errors import operator_error_response
from synth_engine.bootstrapper.schemas.vault import UnsealRequest as UnsealRequest  # re-exported
from synth_engine.shared.cert_metrics import update_cert_expiry_metrics
from synth_engine.shared.db import dispose_engines
from synth_engine.shared.security.audit import get_audit_logger
from synth_engine.shared.security.vault import (
    VaultAlreadyUnsealedError,
    VaultConfigError,
    VaultEmptyPassphraseError,
    VaultState,
)

_logger = logging.getLogger(__name__)


@contextlib.asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncGenerator[None]:
    """FastAPI lifespan — startup validation and shutdown cleanup.

    Args:
        app: FastAPI application (lifespan protocol; unused here).

    Yields:
        None: Control to the application for its lifetime.
    """
    validate_config()
    await asyncio.to_thread(update_cert_expiry_metrics)
    try:
        yield
    finally:
        try:
            get_audit_logger().log_event(
                event_type="SERVER_SHUTDOWN",
                actor="system",
                resource="server",
                action="shutdown",
                details={},
            )
        except (ValueError, OSError, UnicodeError):
            _logger.warning("Shutdown audit event could not be recorded.", exc_info=True)
        try:
            dispose_engines()
        except (OSError, SQLAlchemyError):
            _logger.warning("dispose_engines() failed during shutdown.", exc_info=True)
        try:
            close_redis_client()
        except (OSError, RedisError):
            _logger.warning("close_redis_client() failed during shutdown.", exc_info=True)
        # T75.3 (review fix): In multiprocess mode, signal prometheus_client to
        # remove .db files for this worker so stale data is not aggregated by
        # surviving workers after this process exits cleanly.
        if os.environ.get("PROMETHEUS_MULTIPROC_DIR", "").strip():
            try:
                mark_process_dead(os.getpid())  # type: ignore[no-untyped-call]  # prometheus_client has no py.typed
            except (OSError, ValueError):
                _logger.warning("mark_process_dead() failed during shutdown.", exc_info=True)
        _logger.info("Shutdown cleanup complete.")


def _register_routes(app: FastAPI) -> None:
    """Register ``POST /unseal``. ``GET /health`` is in :mod:`routers.health`.

    Args:
        app: The FastAPI instance to register routes on.
    """

    @app.post("/unseal", tags=["ops"])
    async def unseal_vault(body: UnsealRequest) -> JSONResponse:
        """Unseal the vault from the operator passphrase.

        Args:
            body: JSON body with operator passphrase.

        Returns:
            ``{"status": "unsealed"}`` HTTP 200, or RFC 7807 HTTP 400.
        """
        try:
            # T70.3: convert str passphrase to bytearray so unseal() can zero it.
            passphrase_buf = bytearray(body.passphrase.encode("utf-8"))
            await asyncio.to_thread(VaultState.unseal, passphrase_buf)
        except VaultEmptyPassphraseError as exc:
            return operator_error_response(exc)
        except VaultAlreadyUnsealedError as exc:
            _logger.warning("Vault unseal attempted when already unsealed: %s", exc)
            return JSONResponse(
                status_code=400,
                content={
                    "type": "about:blank",
                    "title": "Vault Already Unsealed",
                    "status": 400,
                    "detail": "The vault is already unsealed. No action required.",
                },
            )
        except VaultConfigError as exc:
            return operator_error_response(exc)
        try:
            get_audit_logger().log_event(
                event_type="VAULT_UNSEAL",
                actor="operator",
                resource="vault",
                action="unseal",
                details={},
            )
        except (ValueError, OSError, UnicodeError):
            _logger.warning("AUDIT_KEY not configured; vault unseal event was not audited.")
        return JSONResponse(content={"status": "unsealed"})
