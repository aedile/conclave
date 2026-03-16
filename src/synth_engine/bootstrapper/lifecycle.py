"""FastAPI lifespan hooks and core ops route registration.

Contains:
- ``UnsealRequest`` — Pydantic model for the /unseal request body.
- ``_lifespan()`` — async context manager wired as the FastAPI lifespan hook;
  runs startup validation via :func:`~synth_engine.bootstrapper.config_validation.validate_config`.
- ``_register_routes()`` — attaches /health and /unseal to the application.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncGenerator

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from synth_engine.bootstrapper.config_validation import validate_config
from synth_engine.shared.security.vault import (
    VaultAlreadyUnsealedError,
    VaultConfigError,
    VaultEmptyPassphraseError,
    VaultState,
)

_logger = logging.getLogger(__name__)


class UnsealRequest(BaseModel):
    """Request body for the /unseal endpoint.

    Attributes:
        passphrase: Operator-provided passphrase used to derive the KEK.
    """

    passphrase: str


@contextlib.asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncGenerator[None]:
    """FastAPI lifespan hook — startup validation and teardown.

    Runs :func:`~synth_engine.bootstrapper.config_validation.validate_config`
    at server startup to enforce fail-fast configuration validation before
    the application accepts any traffic.  This hook is executed by the ASGI
    server (uvicorn) when the process starts — not at import time — so unit
    tests that call :func:`create_app` without a live ASGI server are
    unaffected.

    Args:
        app: The FastAPI application instance (required by FastAPI lifespan
            protocol; unused here but part of the interface contract).

    Yields:
        Control to the application for the duration of its lifetime.
    """
    validate_config()
    yield


def _register_routes(app: FastAPI) -> None:
    """Attach all core ops routes to the application.

    Registers:
    - ``GET /health`` — liveness probe for container orchestrators.
    - ``POST /unseal`` — operator passphrase → vault KEK derivation.

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
            ``{"error_code": "<code>", "detail": "<reason>"}`` with HTTP 400
            on failure.
        """
        try:
            await asyncio.to_thread(VaultState.unseal, body.passphrase)
        except VaultEmptyPassphraseError as exc:
            return JSONResponse(
                content={"error_code": "EMPTY_PASSPHRASE", "detail": str(exc)},
                status_code=400,
            )
        except VaultAlreadyUnsealedError as exc:
            return JSONResponse(
                content={"error_code": "ALREADY_UNSEALED", "detail": str(exc)},
                status_code=400,
            )
        except VaultConfigError as exc:
            return JSONResponse(
                content={"error_code": "CONFIG_ERROR", "detail": str(exc)},
                status_code=400,
            )
        except ValueError as exc:
            # Fallback for unexpected ValueError subclasses
            return JSONResponse(
                content={"error_code": "CONFIG_ERROR", "detail": str(exc)},
                status_code=400,
            )

        # Emit audit event — best-effort; failure must not prevent unsealing
        try:
            from synth_engine.shared.security.audit import get_audit_logger

            audit = get_audit_logger()
            audit.log_event(
                event_type="VAULT_UNSEAL",
                actor="operator",
                resource="vault",
                action="unseal",
                details={},
            )
        except (ValueError, RuntimeError):
            # AUDIT_KEY not configured in this environment — log but continue
            _logger.warning("AUDIT_KEY not configured; vault unseal event was not audited.")

        return JSONResponse(content={"status": "unsealed"})
