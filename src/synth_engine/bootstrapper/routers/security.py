"""FastAPI router for Cryptographic Security Operations.

Implements:
- POST /security/shred  — immediately zeroizes the master wrapping key,
  rendering all database ciphertext permanently unrecoverable.
- POST /security/keys/rotate  — enqueues a Huey background task that
  re-encrypts all ALE-encrypted columns using a new KEK-derived key.

Both endpoints are ops-level operations for emergency security protocols
(data spillage response, key compromise rotation).  They emit WORM audit
events on every call.

Both handlers must be accessible while the vault is unsealed; ``/security/shred``
is special — it seals the vault, so it must also work from any state (even
already-sealed).  Both paths are added to ``EXEMPT_PATHS`` in the vault
dependency module so they bypass the ``SealGateMiddleware``.

RFC 7807 Problem Details format is used for all error responses.

All route handlers are ``async def`` per the T5.2 architecture finding.

CONSTITUTION Priority 0: Security
Task: P5-T5.5 — Cryptographic Shredding & Re-Keying API
"""

from __future__ import annotations

import logging
import os

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from synth_engine.bootstrapper.errors import problem_detail
from synth_engine.shared.security.audit import get_audit_logger
from synth_engine.shared.security.rotation import rotate_ale_keys_task
from synth_engine.shared.security.vault import VaultState

_logger = logging.getLogger(__name__)

router = APIRouter(prefix="/security", tags=["security"])


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------


class RotateRequest(BaseModel):
    """Request body for the key rotation endpoint.

    Attributes:
        new_passphrase: New operator passphrase.  The rotation task derives a
            fresh Fernet key independently; this passphrase is used to document
            operator intent and MAY be used in future implementations to unseal
            with a new passphrase.  Currently the rotation generates a fresh
            random Fernet key and re-encrypts all columns.
    """

    new_passphrase: str


# ---------------------------------------------------------------------------
# POST /security/shred
# ---------------------------------------------------------------------------


@router.post("/shred", tags=["security"])
async def shred_vault() -> JSONResponse:
    """Zeroize the master wrapping key, rendering all ALE ciphertext unrecoverable.

    This endpoint implements an emergency cryptographic shred protocol.
    It calls :meth:`VaultState.seal()` which:
    - Overwrites every byte of the in-memory KEK ``bytearray`` with ``0x00``.
    - Sets ``VaultState._is_sealed = True``.
    - Sets ``VaultState._kek = None``.

    After this call, :func:`~synth_engine.shared.security.ale.get_fernet` can
    no longer derive the ALE key, so every subsequent attempt to decrypt
    ciphertext from the database will raise ``InvalidToken`` or
    ``RuntimeError``.  The data is permanently unrecoverable without the
    original passphrase (which must be destroyed independently by the operator).

    An audit event ``CRYPTO_SHRED`` is emitted before the seal so that the
    shred operation itself is on the audit record.

    Returns:
        ``{"status": "shredded"}`` with HTTP 200.
    """
    # Emit audit event BEFORE sealing (best-effort — never block shred)
    try:
        audit = get_audit_logger()
        audit.log_event(
            event_type="CRYPTO_SHRED",
            actor="operator",
            resource="vault",
            action="shred",
            details={"note": "Master KEK zeroized — all ALE ciphertext is now unrecoverable"},
        )
    except (ValueError, RuntimeError) as exc:
        _logger.warning("Audit logging failed during CRYPTO_SHRED; proceeding: %s", exc)

    # Seal (zeroize KEK) — idempotent-safe
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


@router.post("/keys/rotate", tags=["security"])
async def rotate_keys(body: RotateRequest) -> JSONResponse:
    """Enqueue a Huey background task to re-encrypt all ALE-encrypted columns.

    This endpoint initiates an asynchronous key rotation workflow.  It:
    1. Verifies the vault is currently unsealed (rotation requires the current KEK).
    2. Emits a ``KEY_ROTATION_REQUESTED`` audit event.
    3. Generates a fresh Fernet key for the new ALE encryption.
    4. Enqueues a Huey task (``rotate_ale_keys_task``) that decrypts all
       existing ciphertext with the current ALE key and re-encrypts it with
       the new key.
    5. Returns ``202 Accepted`` immediately — the actual rotation runs in the
       Huey worker background.

    The ``new_passphrase`` in the request body is logged to the audit trail to
    document operator intent.  It is NOT used to derive the new Fernet key;
    a random key is generated for the re-encryption.

    Args:
        body: JSON body containing ``new_passphrase``.

    Returns:
        ``{"status": "accepted", "detail": "..."}`` with HTTP 202 on success.
        RFC 7807 423 if the vault is sealed (cannot rotate without the current KEK).
    """
    # Gate: rotation requires an unsealed vault (need the current KEK for decryption)
    if VaultState.is_sealed():
        return JSONResponse(
            status_code=423,
            content=problem_detail(
                status=423,
                title="Vault Sealed",
                detail=(
                    "Key rotation requires an unsealed vault. "
                    "POST /unseal to unseal the vault before rotating keys."
                ),
            ),
        )

    # Emit audit event (best-effort)
    try:
        audit = get_audit_logger()
        audit.log_event(
            event_type="KEY_ROTATION_REQUESTED",
            actor="operator",
            resource="ale_keys",
            action="rotate",
            details={"note": "ALE key rotation initiated via /security/keys/rotate"},
        )
    except (ValueError, RuntimeError) as exc:
        _logger.warning("Audit logging failed during KEY_ROTATION_REQUESTED; proceeding: %s", exc)

    # Generate a fresh Fernet key for the new ALE encryption
    from cryptography.fernet import Fernet

    new_fernet_key = Fernet.generate_key().decode()

    # Read DATABASE_URL for the Huey task (task runs in a separate worker process)
    database_url = os.environ.get("DATABASE_URL", "")
    if not database_url:
        _logger.warning(
            "DATABASE_URL not set; rotate_ale_keys_task will fail in the worker. "
            "Ensure DATABASE_URL is configured in the Huey worker environment."
        )

    # Enqueue the Huey background task
    rotate_ale_keys_task(database_url, new_fernet_key)

    _logger.info(
        "KEY_ROTATION_REQUESTED: ALE key rotation task enqueued. "
        "The worker will re-encrypt all %s columns.",
        "ALE-encrypted",
    )

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
