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
- Requires JWT authentication via ``get_current_user`` dependency (P79-T79.2).
- Self-erasure only: ``body.subject_id`` must equal ``current_user.user_id``
  (JWT ``sub``). Cross-user erasure returns 403 with an RFC 7807 error. The
  IDOR check fires BEFORE the vault-sealed check to prevent information
  disclosure about vault state to unauthorized callers (T69.6, ADV-P68-01).
- ``org_id`` is derived exclusively from the verified JWT — HTTP headers
  (e.g., ``X-Org-ID``) are intentionally ignored (ATTACK-02 mitigation).
- Cross-user attempts emit an audit event for intrusion detection. The
  target ``subject_id`` is intentionally omitted from the audit payload (PII).
- Vault-sealed state returns 423 Locked — ALE-encrypted data cannot be
  reliably identified for deletion when the vault is sealed.
- Rate limit: erasure requests fall under the general rate limit tier
  (60/minute per operator) enforced by :class:`RateLimitGateMiddleware`.
  A sub-1/minute natural limit emerges from the authentication requirement
  and the operation cost; the general tier is the nearest configured tier.
- The subject identifier is never written into the audit event details
  (CONSTITUTION Priority 0: no PII in audit payloads).
- RFC 7807 Problem Details format for all error responses.
- ``subject_id`` has ``min_length=1`` to prevent bulk-deletion via an empty
  identifier (QA-B1 + DevOps-B1 review fix).

Boundary constraints (import-linter enforced)
---------------------------------------------
- ``bootstrapper/`` may import from ``shared/`` and ``modules/``.
- ``Connection`` model is injected into ``ErasureService`` here, keeping
  ``modules/synthesizer/erasure.py`` free of any ``bootstrapper/`` import.
- The DI-provided ``Session`` is passed directly to ``ErasureService``,
  eliminating the ``session.get_bind()`` leaky abstraction (ARCH-F7 review fix).

Task: T41.2 — GDPR erasure endpoint
Task: T69.6 — Self-erasure IDOR guard
Task: P79-T79.2 — Migrate routers to TenantContext (org_id filtering)

CONSTITUTION Priority 0: Security — IDOR guard, vault gate, PII-safe audit
CONSTITUTION Priority 5: Code Quality — strict typing, Google docstrings
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlmodel import Session

from synth_engine.bootstrapper.dependencies.db import get_db_session
from synth_engine.bootstrapper.dependencies.tenant import TenantContext, get_current_user
from synth_engine.bootstrapper.errors import problem_detail
from synth_engine.bootstrapper.openapi_metadata import COMMON_ERROR_RESPONSES
from synth_engine.bootstrapper.schemas.connections import Connection
from synth_engine.modules.synthesizer.lifecycle.erasure import DeletionManifest, ErasureService
from synth_engine.shared.observability import AUDIT_WRITE_FAILURE_TOTAL
from synth_engine.shared.security.audit import get_audit_logger
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
            Must be at least 1 character to prevent bulk-deletion via an empty
            identifier (QA-B1 + DevOps-B1 review fix).
            Must equal the authenticated user's JWT ``sub`` claim
            (self-erasure only, T69.6).
    """

    subject_id: str = Field(
        min_length=1,
        max_length=255,
        description=(
            "Opaque data subject identifier used to locate records for deletion. "
            "Must match the owner_id stored on connection and job records. "
            "Must equal the authenticated user's JWT sub claim (self-erasure only)."
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
        audit_logged: ``True`` when the audit chain entry was written
            successfully.  ``False`` indicates partial erasure — the DB
            records were deleted but the audit trail entry failed.
    """

    subject_id: str
    deleted_connections: int
    deleted_jobs: int
    retained_synthesized_output: bool
    retained_audit_trail: bool
    retained_synthesized_output_justification: str
    retained_audit_trail_justification: str
    audit_logged: bool

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
            audit_logged=manifest.audit_logged,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _check_erasure_idor(body_subject_id: str, user_id: str) -> JSONResponse | None:
    """Return 404 if user is attempting cross-user erasure (T69.6, P79-F1).

    Returns 404 (not 403) to avoid leaking the existence of other users' data.

    Emits an audit event for intrusion detection.  The target subject_id is
    intentionally omitted (PII protection).

    Args:
        body_subject_id: The ``subject_id`` from the request body.
        user_id: Authenticated user's JWT sub claim (from TenantContext).

    Returns:
        A 404 JSONResponse if blocked; None if the check passes.
    """
    if body_subject_id == user_id:
        return None
    _logger.warning("Cross-user erasure attempt blocked: actor=%s (subject_id withheld)", user_id)
    try:
        get_audit_logger().log_event(
            event_type="COMPLIANCE_ERASURE_IDOR_ATTEMPT",
            actor=user_id,
            resource="data_subject",
            action="erasure_blocked_idor",
            details={"reason": "subject_id does not match authenticated user"},
        )
    except (ValueError, OSError, UnicodeError):
        AUDIT_WRITE_FAILURE_TOTAL.labels(router="compliance", endpoint="/compliance/erasure").inc()
        _logger.exception("Audit logging failed for IDOR erasure attempt (actor=%s).", user_id)
    return JSONResponse(
        status_code=404,
        content=problem_detail(
            status=404,
            title="Not Found",
            detail="The requested data subject was not found.",
        ),
        media_type="application/problem+json",
    )


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------
#
# Transaction handling note (T62.1 context):
# This router does NOT use explicit session.commit() wrapping around the
# route handler body.  The DB writes for erasure are delegated entirely to
# ErasureService, which controls its own transaction boundaries.  The DI-
# provided Session is passed into ErasureService and committed there.
# Adding a redundant outer commit here would create a double-commit risk.


@router.delete(
    "/erasure",
    summary="Execute GDPR erasure",
    description=(
        "Delete all synthesis jobs and artifacts for a data subject. "
        "Emits a WORM-audited compliance event. "
        "Subject ID must equal the authenticated user's own identity (self-erasure only)."
    ),
    responses=COMMON_ERROR_RESPONSES,
    response_model=ErasureResponse,
)
def erasure(
    body: ErasureRequest,
    session: Annotated[Session, Depends(get_db_session)],
    current_user: Annotated[TenantContext, Depends(get_current_user)],
) -> ErasureResponse | JSONResponse:
    """Execute a GDPR Right-to-Erasure / CCPA deletion request.

    IDOR check fires first (T69.6): ``subject_id`` must equal the
    authenticated user's ``user_id`` (JWT ``sub`` claim).
    Vault-sealed check second.  Audit failure does not abort erasure.

    Args:
        body: JSON body with ``subject_id`` (must match user's JWT sub claim).
        session: Database session (injected by FastAPI DI).
        current_user: Resolved tenant identity (org_id, user_id, role) from JWT.

    Returns:
        :class:`ErasureResponse` on success, RFC 7807 403 on IDOR, 423 if sealed.
    """
    idor_err = _check_erasure_idor(body.subject_id, current_user.user_id)
    if idor_err is not None:
        return idor_err

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

    manifest = ErasureService(session=session, connection_model=Connection).execute_erasure(
        subject_id=body.subject_id,
        actor=current_user.user_id,
        org_id=current_user.org_id,
    )
    response = ErasureResponse.from_manifest(manifest)
    if not manifest.audit_logged:
        _logger.warning(
            "Erasure audit log failed for subject (ID withheld). "
            "DB records deleted but audit chain entry was not written. "
            "Manual audit chain reconciliation required.",
        )
    return response
