"""FastAPI lifespan hooks and core ops route registration.

Contains:
- ``UnsealRequest`` — re-exported from :mod:`schemas.vault` for backward compatibility.
- ``_lifespan()`` — async context manager wired as the FastAPI lifespan hook;
  runs startup validation via :func:`~synth_engine.bootstrapper.config_validation.validate_config`
  and initialises certificate expiry Prometheus metrics (T46.3).
- ``_register_routes()`` — attaches /health and /unseal to the application.

Task: P29-T29.3 — Error Message Audience Differentiation
    The /unseal route now returns RFC 7807 format for error responses, using
    OPERATOR_ERROR_MAP for operator-friendly titles and actionable detail messages.
    The legacy ``{"error_code": ..., "detail": ...}`` format has been removed.

Task: T46.3 — Certificate Rotation Without Downtime
    ``_lifespan`` calls ``update_cert_expiry_metrics()`` at startup so that
    the Prometheus gauge is populated on the first scrape.  The call is
    dispatched via ``asyncio.to_thread`` to avoid blocking the event loop
    during synchronous file I/O (Finding 3: T46.3 review).

Task: T47.8 — Add Shutdown Cleanup to Lifespan Hook
    ``_lifespan`` now includes a ``finally`` block that:
    1. Emits a SERVER_SHUTDOWN audit event (best-effort).
    2. Calls ``dispose_engines()`` to release DB connection pools.
    3. Calls ``close_redis_client()`` to release Redis connections.
    Each step is isolated in its own ``try/except`` so a failure in one
    step does not prevent the remaining steps from running.

Task: T60.5 — Move UnsealRequest to schemas/vault.py
    ``UnsealRequest`` now lives in :mod:`synth_engine.bootstrapper.schemas.vault`.
    Re-exported here unconditionally to preserve the existing import contract:
    ``from synth_engine.bootstrapper.lifecycle import UnsealRequest``.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncGenerator

from fastapi import FastAPI
from fastapi.responses import JSONResponse

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
    """FastAPI lifespan hook — startup validation and teardown.

    Runs :func:`~synth_engine.bootstrapper.config_validation.validate_config`
    at server startup to enforce fail-fast configuration validation before
    the application accepts any traffic.  Also initialises certificate expiry
    Prometheus metrics so the gauge is populated on the first scrape rather
    than returning "no data" until the first periodic update.

    ``update_cert_expiry_metrics`` reads cert files from disk and is therefore
    dispatched via ``asyncio.to_thread`` so that synchronous file I/O does not
    block the event loop during startup.

    On shutdown (post-yield), a ``finally`` block runs cleanup steps in order:

    1. Emit a ``SERVER_SHUTDOWN`` audit event (best-effort — a failure here
       is logged as WARNING but does not abort the remaining steps).
    2. Call :func:`~synth_engine.shared.db.dispose_engines` to release all
       DB connection pool resources.
    3. Call :func:`~synth_engine.bootstrapper.dependencies.redis.close_redis_client`
       to release Redis connection pool resources.

    Each cleanup step is wrapped in its own ``try/except`` so that a failure
    in one step cannot prevent subsequent steps from running.  The outer
    ``finally`` block guarantees cleanup runs even on SIGTERM or
    ``KeyboardInterrupt``.

    This hook is executed by the ASGI server (uvicorn) when the process
    starts -- not at import time -- so unit tests that call
    :func:`create_app` without a live ASGI server are unaffected.

    Args:
        app: The FastAPI application instance (required by FastAPI lifespan
            protocol; unused here but part of the interface contract).

    Yields:
        None: Control to the application for the duration of its lifetime.
    """
    validate_config()
    # Populate cert expiry metrics at startup so the first Prometheus scrape
    # has data.  Dispatched via asyncio.to_thread to avoid blocking the event
    # loop on synchronous file I/O.  Failures are logged (not raised) inside
    # update_cert_expiry_metrics.
    await asyncio.to_thread(update_cert_expiry_metrics)
    try:
        yield
    finally:
        # Step 1 — Audit event (best-effort; must not block remaining cleanup)
        try:
            audit = get_audit_logger()
            audit.log_event(
                event_type="SERVER_SHUTDOWN",
                actor="system",
                resource="server",
                action="shutdown",
                details={},
            )
        except Exception:
            _logger.warning("Shutdown audit event could not be recorded.", exc_info=True)

        # Step 2 — Dispose DB engine connection pools
        try:
            dispose_engines()
        except Exception:
            _logger.warning("dispose_engines() failed during shutdown.", exc_info=True)

        # Step 3 — Close Redis connection pool
        try:
            close_redis_client()
        except Exception:
            _logger.warning("close_redis_client() failed during shutdown.", exc_info=True)

        _logger.info("Shutdown cleanup complete.")


def _register_routes(app: FastAPI) -> None:
    """Attach all core ops routes to the application.

    Registers:
    - ``GET /health`` -- liveness probe for container orchestrators.
    - ``POST /unseal`` -- operator passphrase -> vault KEK derivation.

    Args:
        app: The FastAPI instance to register routes on.
    """

    @app.get("/health", tags=["ops"])
    async def health_check() -> JSONResponse:
        """Liveness probe for container orchestrators and load balancers.

        Returns:
            JSON body ``{"status": "ok"}`` with HTTP 200.
        """
        return JSONResponse(content={"status": "ok"})

    @app.post("/unseal", tags=["ops"])
    async def unseal_vault(body: UnsealRequest) -> JSONResponse:
        """Unseal the vault by deriving the KEK from the operator passphrase.

        Reads ``VAULT_SEAL_SALT`` from the environment, runs PBKDF2-HMAC-
        SHA256 (600k iterations) in a thread pool to avoid blocking the event
        loop, stores the result in ephemeral memory, and logs an audit event.

        Args:
            body: JSON body containing the operator passphrase.

        Returns:
            ``{"status": "unsealed"}`` with HTTP 200 on success.
            RFC 7807 Problem Details with HTTP 400 on failure, using
            operator-friendly titles and actionable detail messages.
        """
        try:
            await asyncio.to_thread(VaultState.unseal, body.passphrase)
        except VaultEmptyPassphraseError as exc:
            return operator_error_response(exc)
        except VaultAlreadyUnsealedError as exc:
            # VaultAlreadyUnsealedError is handled inline rather than via
            # OPERATOR_ERROR_MAP because it is an informational 400, not a
            # hard failure -- the operator's desired state (vault unsealed)
            # is already achieved.  A bespoke message makes this distinction
            # clear without adding a map entry that implies a recoverable
            # error requiring corrective action.
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

        # Emit audit event -- best-effort; failure must not prevent unsealing
        try:
            audit = get_audit_logger()
            audit.log_event(
                event_type="VAULT_UNSEAL",
                actor="operator",
                resource="vault",
                action="unseal",
                details={},
            )
        except (ValueError, RuntimeError):
            # AUDIT_KEY not configured in this environment -- log but continue
            _logger.warning("AUDIT_KEY not configured; vault unseal event was not audited.")

        return JSONResponse(content={"status": "unsealed"})
