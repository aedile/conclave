"""FastAPI router for compliance operations — T41.2.

Implements:
- DELETE /compliance/erasure — GDPR Article 17 Right-to-Erasure & CCPA deletion.

The endpoint accepts a data subject identifier and cascades deletion through
connection metadata and synthesis job records referencing that identifier.
Synthesized output (differentially private, non-attributable) and the WORM
audit trail (legally required compliance evidence) are always preserved.

A compliance receipt is returned documenting what was deleted and what was
retained, with GDPR-basis justifications for each retained category.

Security posture
----------------
- Requires operator authentication via ``get_current_operator`` dependency.
- Vault-sealed state returns 423 Locked — ALE-encrypted data cannot be
  reliably identified for deletion when the vault is sealed.
- Rate limit: erasure requests fall under the general rate limit tier
  (60/minute per operator) enforced by :class:`RateLimitGateMiddleware`.
  A sub-1/minute natural limit emerges from the authentication requirement
  and the operation cost; the general tier is the nearest configured tier.
- The subject identifier is never written into the audit event details
  (CONSTITUTION Priority 0: no PII in audit payloads).
- RFC 7807 Problem Details format for all error responses.

Boundary constraints (import-linter enforced)
---------------------------------------------
- ``bootstrapper/`` may import from ``shared/`` and ``modules/``.
- ``Connection`` model is injected into ``ErasureService`` here, keeping
  ``modules/synthesizer/erasure.py`` free of any ``bootstrapper/`` import.

CONSTITUTION Priority 0: Security — vault gate, PII-safe audit
CONSTITUTION Priority 5: Code Quality — strict typing, Google docstrings
Task: T41.2 — Implement GDPR Right-to-Erasure & CCPA Deletion Endpoint
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import Engine
from sqlmodel import Session

from synth_engine.bootstrapper.dependencies.auth import get_current_operator
from synth_engine.bootstrapper.dependencies.db import get_db_session
from synth_engine.bootstrapper.errors import problem_detail
from synth_engine.bootstrapper.schemas.connections import Connection
from synth_engine.modules.synthesizer.erasure import DeletionManifest, ErasureService
from synth_engine.shared.security.vault import VaultState

_logger = logging.getLogger(__name__)

router = APIRouter(prefix="/compliance", tags=["compliance"])


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------


class ErasureRequest(BaseModel):
    """Request body for DELETE /compliance/erasure.

    Attributes:
        subject_id: Opaque data subject identifier (e.g. owner_id, hashed
            email).  Used as a filter key — never logged verbatim.
    """

    subject_id: str = Field(
        description=(
            "Opaque data subject identifier used to locate records for deletion. "
            "Must match the owner_id stored on connection and job records."
        ),
    )


class ErasureResponse(BaseModel):
    """Compliance receipt for DELETE /compliance/erasure.

    Documents what was deleted and what was retained, with legal
    justifications for each retained category.

    Attributes:
        subject_id: The identifier supplied in the request.
        deleted_connections: Number of connection records deleted.
        deleted_jobs: Number of synthesis job records deleted.
        retained_synthesized_output: Always ``True`` — DP output is not PII.
        retained_audit_trail: Always ``True`` — required compliance evidence.
        retained_synthesized_output_justification: GDPR-basis explanation.
        retained_audit_trail_justification: GDPR-basis explanation.
    """

    subject_id: str
    deleted_connections: int
    deleted_jobs: int
    retained_synthesized_output: bool
    retained_audit_trail: bool
    retained_synthesized_output_justification: str
    retained_audit_trail_justification: str

    model_config = {"from_attributes": True}

    @classmethod
    def from_manifest(cls, manifest: DeletionManifest) -> ErasureResponse:
        """Build a response from a :class:`DeletionManifest`.

        Args:
            manifest: The deletion manifest returned by :class:`ErasureService`.

        Returns:
            :class:`ErasureResponse` populated from the manifest.
        """
        return cls(
            subject_id=manifest.subject_id,
            deleted_connections=manifest.deleted_connections,
            deleted_jobs=manifest.deleted_jobs,
            retained_synthesized_output=manifest.retained_synthesized_output,
            retained_audit_trail=manifest.retained_audit_trail,
            retained_synthesized_output_justification=(
                manifest.retained_synthesized_output_justification
            ),
            retained_audit_trail_justification=manifest.retained_audit_trail_justification,
        )


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------


@router.delete("/erasure", response_model=ErasureResponse)
def erasure(
    body: ErasureRequest,
    session: Annotated[Session, Depends(get_db_session)],
    current_operator: Annotated[str, Depends(get_current_operator)],
) -> ErasureResponse | JSONResponse:
    """Execute a GDPR Right-to-Erasure / CCPA deletion request.

    Cascades deletion through connection metadata and synthesis job records
    whose ``owner_id`` matches ``body.subject_id``.  Synthesized output
    and the WORM audit trail are always preserved (with justifications in
    the compliance receipt).

    If the vault is sealed, the request is rejected with 423 Locked because
    ALE-encrypted fields cannot be reliably identified for deletion without
    the vault key.

    Every successful erasure (including requests matching zero records) is
    logged to the WORM audit trail.  Audit failure does not abort erasure.

    Args:
        body: JSON body with a single ``subject_id`` string field.
        session: Database session (injected by FastAPI DI).
        current_operator: Authenticated operator sub claim (injected by FastAPI DI).

    Returns:
        :class:`ErasureResponse` compliance receipt on success, or RFC 7807
        423 response if the vault is sealed.
    """
    # Vault-sealed guard: ALE-encrypted fields cannot be identified without the KEK.
    if VaultState.is_sealed():
        return JSONResponse(
            status_code=423,
            content=problem_detail(
                status=423,
                title="Vault Is Sealed",
                detail=(
                    "Erasure cannot proceed while the vault is sealed. "
                    "ALE-encrypted fields cannot be identified for deletion "
                    "without the vault key. POST /unseal to unlock."
                ),
            ),
            media_type="application/problem+json",
        )

    engine = session.get_bind()
    if not isinstance(engine, Engine):
        _logger.error(
            "compliance/erasure: session is not bound to a SQLAlchemy Engine "
            "(got %s). Erasure cannot proceed.",
            type(engine).__name__,
        )
        return JSONResponse(
            status_code=500,
            content=problem_detail(
                status=500,
                title="Internal Server Error",
                detail="Database session configuration error. Contact your administrator.",
            ),
            media_type="application/problem+json",
        )

    service = ErasureService(
        session_factory=engine,
        connection_model=Connection,
    )
    manifest = service.execute_erasure(
        subject_id=body.subject_id,
        actor=current_operator,
    )

    return ErasureResponse.from_manifest(manifest)
