"""FastAPI router for Cryptographic Security Operations.

Implements:
- POST /security/shred  — immediately zeroizes the master wrapping key,
  rendering all database ciphertext permanently unrecoverable.
- POST /security/keys/rotate  — enqueues a Huey background task that
  re-encrypts all ALE-encrypted columns using a new KEK-derived key.

Both endpoints emit WORM audit events on every call.

Audit-before-destructive (T68.3)
---------------------------------
Both endpoints emit their WORM audit event BEFORE any destructive side effect:
- ``/security/shred``: audit fires before ``VaultState.seal()``.
- ``/security/keys/rotate``: audit fires before ``rotate_ale_keys_task()`` is enqueued.

Layered exemption model (P50 review fix)
-----------------------------------------
``/security/shred`` is exempt from SealGateMiddleware and LicenseGateMiddleware.
``/security/keys/rotate`` requires an unsealed vault.  Both require JWT with
``security:admin`` scope (ADV-P47-04).

RFC 7807 Problem Details format is used for all error responses.
All route handlers are ``async def`` per the T5.2 architecture finding.

CONSTITUTION Priority 0: Security
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from synth_engine.bootstrapper.dependencies.auth import require_scope
from synth_engine.bootstrapper.errors import problem_detail
from synth_engine.bootstrapper.openapi_metadata import COMMON_ERROR_RESPONSES
from synth_engine.shared.observability import AUDIT_WRITE_FAILURE_TOTAL
from synth_engine.shared.security.ale import get_fernet
from synth_engine.shared.security.audit import get_audit_logger
from synth_engine.shared.security.rotation import rotate_ale_keys_task
from synth_engine.shared.security.vault import VaultState

_logger = logging.getLogger(__name__)

router = APIRouter(prefix="/security", tags=["security"])

# T71.5 — Use shared AUDIT_WRITE_FAILURE_TOTAL counter from shared/observability.py.


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------


class RotateRequest(BaseModel):
    """Request body for the key rotation endpoint.

    Attributes:
        new_passphrase: New operator passphrase (1-1024 chars).  Bounded to
            prevent oversized-input DoS (P59 Red-team F3).
    """

    new_passphrase: str = Field(..., min_length=1, max_length=1024)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _emit_shred_audit(operator: str) -> JSONResponse | None:
    """Emit CRYPTO_SHRED audit event before sealing (T68.3).

    Args:
        operator: Authenticated operator sub claim.

    Returns:
        None on success; a 500 JSONResponse on audit write failure.
    """
    try:
        get_audit_logger().log_event(
            event_type="CRYPTO_SHRED",
            actor=operator,
            resource="vault",
            action="shred",
            details={"note": "Master KEK zeroized — all ALE ciphertext is now unrecoverable"},
        )
        return None
    except (ValueError, OSError, UnicodeError):
        AUDIT_WRITE_FAILURE_TOTAL.labels(router="security", endpoint="/security/shred").inc()
        _logger.exception("Audit logging failed during CRYPTO_SHRED; aborting shred (T68.3)")
        return JSONResponse(
            status_code=500,
            content={
                "type": "about:blank",
                "title": "Internal Server Error",
                "status": 500,
                "detail": "Audit write failed. Cryptographic shred was NOT performed.",
            },
        )


def _emit_rotation_audit(operator: str, passphrase_provided: bool) -> JSONResponse | None:
    """Emit KEY_ROTATION_REQUESTED audit event before enqueuing (T68.3).

    Args:
        operator: Authenticated operator sub claim.
        passphrase_provided: Whether ``new_passphrase`` was supplied.

    Returns:
        None on success; a 500 JSONResponse on audit write failure.
    """
    try:
        get_audit_logger().log_event(
            event_type="KEY_ROTATION_REQUESTED",
            actor=operator,
            resource="ale_keys",
            action="rotate",
            details={
                "note": "ALE key rotation initiated via /security/keys/rotate",
                "passphrase_provided": str(passphrase_provided),
            },
        )
        return None
    except (ValueError, OSError, UnicodeError):
        AUDIT_WRITE_FAILURE_TOTAL.labels(router="security", endpoint="/security/keys/rotate").inc()
        _logger.exception("Audit logging failed during KEY_ROTATION_REQUESTED; aborting (T68.3)")
        return JSONResponse(
            status_code=500,
            content={
                "type": "about:blank",
                "title": "Internal Server Error",
                "status": 500,
                "detail": "Audit write failed. Key rotation task was NOT enqueued.",
            },
        )


def _enqueue_rotation_task() -> None:
    """Generate a new Fernet key, wrap it with the vault KEK, and enqueue rotation.

    Reads DATABASE_URL from settings.  Logs a WARNING if absent.  The key is
    wrapped before being passed to the broker so it is never stored in plaintext.
    """
    from cryptography.fernet import Fernet

    from synth_engine.shared.settings import get_settings

    new_fernet_key = Fernet.generate_key()
    wrapped_key = get_fernet().encrypt(new_fernet_key).decode()
    database_url = get_settings().database_url or ""
    if not database_url:
        _logger.warning(
            "DATABASE_URL not set; rotate_ale_keys_task will fail in the worker. "
            "Ensure DATABASE_URL is configured in the Huey worker environment."
        )
    rotate_ale_keys_task(database_url, wrapped_key)
    _logger.info("KEY_ROTATION_REQUESTED: ALE key rotation task enqueued.")


# ---------------------------------------------------------------------------
# POST /security/shred
# ---------------------------------------------------------------------------


@router.post(
    "/shred",
    summary="Emergency cryptographic shred",
    description=(
        "Destroy all encryption keys and artifacts. "
        "IRREVERSIBLE. Reachable even when vault is sealed."
    ),
    responses=COMMON_ERROR_RESPONSES,
    tags=["security"],
)
async def shred_vault(
    current_operator: Annotated[str, Depends(require_scope("security:admin"))],
) -> JSONResponse:
    """Zeroize the master wrapping key, rendering all ALE ciphertext unrecoverable.

    T68.3: Audit emitted BEFORE the vault is sealed. If audit fails, 500 is
    returned and the vault is NOT sealed. Requires ``security:admin`` scope.

    Args:
        current_operator: Authenticated operator sub claim (security:admin scope).

    Returns:
        ``{"status": "shredded"}`` with HTTP 200, or RFC 7807 500 on audit failure.
    """
    audit_err = _emit_shred_audit(current_operator)
    if audit_err is not None:
        return audit_err
    VaultState.seal()
    _logger.warning(
        "CRYPTO_SHRED executed: vault KEK zeroized. "
        "All ALE-encrypted ciphertext is now permanently unrecoverable."
    )
    return JSONResponse(
        status_code=200,
        content={
            "status": "shredded",
            "detail": (
                "Master KEK has been zeroized. "
                "All ALE-encrypted ciphertext is permanently unrecoverable."
            ),
        },
    )


# ---------------------------------------------------------------------------
# POST /security/keys/rotate
# ---------------------------------------------------------------------------


@router.post(
    "/keys/rotate",
    summary="Rotate encryption keys",
    description="Rotate the vault Key Encryption Key. Requires an unsealed vault.",
    responses=COMMON_ERROR_RESPONSES,
    tags=["security"],
)
async def rotate_keys(
    body: RotateRequest,
    current_operator: Annotated[str, Depends(require_scope("security:admin"))],
) -> JSONResponse:
    """Enqueue a Huey background task to re-encrypt all ALE-encrypted columns.

    T68.3: Audit emitted BEFORE task is enqueued.  Requires unsealed vault.

    Args:
        body: JSON body containing ``new_passphrase``.
        current_operator: Authenticated operator sub claim (security:admin scope).

    Returns:
        HTTP 202 on success; RFC 7807 423 if vault sealed; 500 on audit failure.
    """
    if VaultState.is_sealed():
        return JSONResponse(
            status_code=423,
            content=problem_detail(
                status=423,
                title="Vault Sealed",
                detail="Key rotation requires an unsealed vault. POST /unseal first.",
            ),
        )
    audit_err = _emit_rotation_audit(current_operator, bool(body.new_passphrase))
    if audit_err is not None:
        return audit_err
    _enqueue_rotation_task()
    return JSONResponse(
        status_code=202,
        content={
            "status": "accepted",
            "detail": (
                "Key rotation task enqueued. "
                "All ALE-encrypted columns will be re-encrypted in the background."
            ),
        },
    )
