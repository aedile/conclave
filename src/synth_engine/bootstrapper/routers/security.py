"""FastAPI router for Cryptographic Security Operations.

Implements:
- POST /security/shred  — immediately zeroizes the master wrapping key,
  rendering all database ciphertext permanently unrecoverable.
- POST /security/keys/rotate  — enqueues a Huey background task that
  re-encrypts all ALE-encrypted columns using a new KEK-derived key.

Both endpoints are ops-level operations for emergency security protocols
(data spillage response, key compromise rotation).  They emit WORM audit
events on every call.

Layered exemption model (P50 review fix)
-----------------------------------------
``/security/shred`` is special — it seals the vault and must work from ANY
state (even when already sealed) for emergency response to key compromise.
``/security/keys/rotate`` requires an unsealed vault to access the current KEK.

Middleware exemption by layer:

- **SealGateMiddleware** (vault gate): only ``/security/shred`` is exempt via
  ``SEAL_EXEMPT_PATHS``.  ``/security/keys/rotate`` is blocked with 423 when
  sealed (the correct posture — rotation cannot proceed without the KEK).
- **LicenseGateMiddleware** (license gate): ``/security/shred`` is exempt via
  ``SEAL_EXEMPT_PATHS`` so emergency shred works without a license.
- **AuthenticationGateMiddleware** (auth gate): NEITHER route is exempt.  Both
  require a valid JWT with ``security:admin`` scope (ADV-P47-04).

Route-level authentication is enforced via the
:func:`~synth_engine.bootstrapper.dependencies.auth.get_current_operator`
dependency on both endpoints (ADV-022).

Scope-based authorization is enforced via
:func:`~synth_engine.bootstrapper.dependencies.auth.require_scope` on both
endpoints.  Both ``/security/shred`` and ``/security/keys/rotate`` require the
``security:admin`` scope (T47.1).

The authenticated operator's sub claim is used as the audit actor identity —
replacing the previous hardcoded ``"operator"`` literal.

RFC 7807 Problem Details format is used for all error responses.

All route handlers are ``async def`` per the T5.2 architecture finding.

CONSTITUTION Priority 0: Security
Task: P5-T5.5 — Cryptographic Shredding & Re-Keying API
Task: T47.1 — Scope-based auth for security endpoints
Task: P50 review fix — restore /security/shred vault-layer bypass (layered model)
Task: T59.3 — OpenAPI Documentation Enrichment
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from synth_engine.bootstrapper.dependencies.auth import require_scope
from synth_engine.bootstrapper.errors import problem_detail
from synth_engine.bootstrapper.openapi_metadata import COMMON_ERROR_RESPONSES
from synth_engine.shared.security.ale import get_fernet
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
    shred operation itself is on the audit record.  The authenticated operator's
    JWT sub claim is used as the audit actor identity.

    Requires scope: ``security:admin`` (T47.1).

    Args:
        current_operator: Authenticated operator sub claim, verified to hold
            the ``security:admin`` scope (injected by FastAPI DI).

    Returns:
        ``{"status": "shredded"}`` with HTTP 200.
    """
    # Emit audit event BEFORE sealing (best-effort — never block shred)
    try:
        audit = get_audit_logger()
        audit.log_event(
            event_type="CRYPTO_SHRED",
            actor=current_operator,
            resource="vault",
            action="shred",
            details={"note": "Master KEK zeroized — all ALE ciphertext is now unrecoverable"},
        )
    except ValueError as exc:
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

    This endpoint initiates an asynchronous key rotation workflow.  It:
    1. Verifies the vault is currently unsealed (rotation requires the current KEK).
    2. Emits a ``KEY_ROTATION_REQUESTED`` audit event.
    3. Generates a fresh Fernet key for the new ALE encryption.
    4. Wraps the new Fernet key using the current vault KEK via Fernet wrapping
       so that it is never passed to the broker in plaintext.
    5. Enqueues a Huey task (``rotate_ale_keys_task``) that decrypts all
       existing ciphertext with the current ALE key and re-encrypts it with
       the new key.
    6. Returns ``202 Accepted`` immediately — the actual rotation runs in the
       Huey worker background.

    The presence of ``new_passphrase`` in the request body is noted in the
    audit trail to document operator intent.  It is NOT used to derive the
    new Fernet key; a random key is generated for the re-encryption.

    Requires scope: ``security:admin`` (T47.1).

    The authenticated operator's JWT sub claim is used as the audit actor
    identity — replacing the previous hardcoded ``"operator"`` literal.

    Args:
        body: JSON body containing ``new_passphrase``.
        current_operator: Authenticated operator sub claim, verified to hold
            the ``security:admin`` scope (injected by FastAPI DI).

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
            actor=current_operator,
            resource="ale_keys",
            action="rotate",
            details={
                "note": "ALE key rotation initiated via /security/keys/rotate",
                "passphrase_provided": str(bool(body.new_passphrase)),
            },
        )
    except ValueError as exc:
        _logger.warning("Audit logging failed during KEY_ROTATION_REQUESTED; proceeding: %s", exc)

    # Generate a fresh Fernet key for the new ALE encryption
    from cryptography.fernet import Fernet

    new_fernet_key = Fernet.generate_key()

    # Wrap the new key with the current vault KEK so it is never stored in the
    # broker (Redis) in plaintext.  The Huey worker unwraps it using the same
    # vault Fernet before constructing the new Fernet instance.
    wrapped_key = get_fernet().encrypt(new_fernet_key).decode()

    # Read DATABASE_URL for the Huey task (task runs in a separate worker process)
    from synth_engine.shared.settings import get_settings

    database_url = get_settings().database_url or ""
    if not database_url:
        _logger.warning(
            "DATABASE_URL not set; rotate_ale_keys_task will fail in the worker. "
            "Ensure DATABASE_URL is configured in the Huey worker environment."
        )

    # Enqueue the Huey background task with the KEK-wrapped new key
    rotate_ale_keys_task(database_url, wrapped_key)

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
