"""GDPR Right-to-Erasure & CCPA Deletion service — T41.2.

Implements the data subject deletion service.  The service cascades deletion
through connection metadata and synthesis job records that reference a given
subject identifier, while explicitly preserving:

- Synthesized output: differentially private output is non-attributable and
  is NOT PII; retaining it does not violate the right to erasure.
- Audit trail: required for compliance proof per GDPR Article 17(3)(b) and
  as evidence of the erasure itself.  The audit trail records are WORM-signed
  and must never be deleted.

Security properties
-------------------
- The subject identifier is treated as a PII pointer.  It is used only as a
  DB query filter and is **never** written verbatim into the audit event
  details (CONSTITUTION Priority 0).
- Audit logging uses ``get_audit_logger()`` singleton for WORM chain
  integrity.  Audit failure is caught and logged; it must never abort a
  deletion that has already committed to the database.

Boundary constraints (import-linter enforced)
---------------------------------------------
- This module is in ``modules/synthesizer/`` and MUST NOT import from
  ``bootstrapper/`` or any other ``modules/`` package.
- It imports from ``shared/`` (audit logger) only.
- The ``Connection`` model from ``bootstrapper/schemas/connections`` is
  provided by the caller (compliance router) via constructor injection,
  keeping this file free of any ``bootstrapper/`` import.

CONSTITUTION Priority 0: Security — PII-safe audit, cascade deletion
CONSTITUTION Priority 5: Code Quality — strict typing, Google docstrings
Task: T41.2 — Implement GDPR Right-to-Erasure & CCPA Deletion Endpoint
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from sqlalchemy import Engine
from sqlmodel import Session, select

from synth_engine.modules.synthesizer.job_models import SynthesisJob
from synth_engine.shared.security.audit import get_audit_logger

_logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Justification constants (GDPR Article 17(3) exceptions)
# ---------------------------------------------------------------------------

_SYNTH_OUTPUT_JUSTIFICATION: str = (
    "Synthesized output is differentially private and non-attributable. "
    "It does not constitute personal data under GDPR Article 4(1) and is "
    "therefore not subject to the right to erasure."
)

_AUDIT_TRAIL_JUSTIFICATION: str = (
    "The audit trail is required for compliance proof under GDPR Article 17(3)(b) "
    "(processing is necessary for compliance with a legal obligation). "
    "The WORM hash-chain signature makes selective deletion detectable."
)


# ---------------------------------------------------------------------------
# DeletionManifest
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DeletionManifest:
    """Immutable record of what was deleted and what was retained.

    Returned by :meth:`ErasureService.execute_erasure` and serialised into
    the compliance receipt HTTP response.

    Attributes:
        subject_id: The identifier used to locate and delete records.
        deleted_connections: Number of connection records deleted.
        deleted_jobs: Number of synthesis job records deleted.
        retained_synthesized_output: Always ``True`` — DP output is not PII.
        retained_audit_trail: Always ``True`` — required for compliance proof.
        retained_synthesized_output_justification: Human-readable GDPR basis.
        retained_audit_trail_justification: Human-readable GDPR basis.
    """

    subject_id: str
    deleted_connections: int
    deleted_jobs: int
    retained_synthesized_output: bool
    retained_audit_trail: bool
    retained_synthesized_output_justification: str
    retained_audit_trail_justification: str


# ---------------------------------------------------------------------------
# ErasureService
# ---------------------------------------------------------------------------


class ErasureService:
    """Service that executes GDPR / CCPA data subject erasure requests.

    Cascades deletion through all connection metadata and synthesis job
    records whose ``owner_id`` matches the given subject identifier.
    Synthesized output files and the WORM audit trail are always preserved.

    Args:
        session_factory: SQLAlchemy :class:`~sqlalchemy.Engine` used to open
            a :class:`Session` for the deletion transaction.
        connection_model: The SQLModel class for connection records.
            Must have an ``owner_id`` column.  Provided by the caller
            (compliance router) via constructor injection so that this
            module never imports from ``bootstrapper/``.  If ``None``,
            connection deletion is skipped.
    """

    def __init__(
        self,
        *,
        session_factory: Engine,
        connection_model: Any | None = None,
    ) -> None:
        self._engine = session_factory
        self._connection_model = connection_model

    def execute_erasure(
        self,
        *,
        subject_id: str,
        actor: str,
    ) -> DeletionManifest:
        """Delete all records referencing the data subject and return a manifest.

        Deletes:
        - :class:`~synth_engine.modules.synthesizer.job_models.SynthesisJob`
          records whose ``owner_id == subject_id``.
        - Connection records whose ``owner_id == subject_id`` (when a
          ``connection_model`` was provided at construction).

        Preserves:
        - Synthesized output files (DP-protected, non-attributable).
        - The WORM audit trail (legally required compliance evidence).

        The erasure event is logged to the audit trail with deletion counts.
        The subject identifier is **never** written into the audit event
        details (CONSTITUTION Priority 0: no PII in audit payloads).

        Audit failure is caught; an incomplete audit log must never abort a
        deletion that has already committed to the database.

        Args:
            subject_id: Opaque identifier for the data subject (e.g. owner_id,
                hashed email).  Used only as a DB filter — never logged.
            actor: Identity of the operator or system performing the request.
                Written into the audit event ``actor`` field.

        Returns:
            A :class:`DeletionManifest` documenting the counts of deleted
            and retained records with their legal justifications.
        """
        deleted_jobs: int = 0
        deleted_connections: int = 0

        with Session(self._engine) as session:
            # --- Delete matching SynthesisJobs ---
            jobs_to_delete = session.exec(
                select(SynthesisJob).where(SynthesisJob.owner_id == subject_id)
            ).all()
            for job in jobs_to_delete:
                session.delete(job)
                deleted_jobs += 1

            # --- Delete matching Connections (if model is available) ---
            if self._connection_model is not None:
                conns_to_delete = session.exec(
                    select(self._connection_model).where(
                        self._connection_model.owner_id == subject_id
                    )
                ).all()
                for conn in conns_to_delete:
                    session.delete(conn)
                    deleted_connections += 1

            session.commit()

        _logger.info(
            "GDPR erasure completed: deleted_jobs=%d deleted_connections=%d actor=%s",
            deleted_jobs,
            deleted_connections,
            actor,
        )

        # Audit the erasure.  Never include the subject_id value in details —
        # it may be PII (e.g. a hashed email).  Record only deletion counts.
        try:
            get_audit_logger().log_event(
                event_type="GDPR_ERASURE",
                actor=actor,
                resource="data_subject",
                action="erasure",
                details={
                    "deleted_jobs": str(deleted_jobs),
                    "deleted_connections": str(deleted_connections),
                    "retained_synthesized_output": "true",
                    "retained_audit_trail": "true",
                },
            )
        except Exception:
            # Audit failure must never abort a completed deletion.
            # The DB write committed; failing here would leave the operator
            # with no feedback about the erasure's success.
            _logger.exception(
                "Audit logging failed for GDPR erasure (actor=%s); DB deletion already committed.",
                actor,
            )

        return DeletionManifest(
            subject_id=subject_id,
            deleted_connections=deleted_connections,
            deleted_jobs=deleted_jobs,
            retained_synthesized_output=True,
            retained_audit_trail=True,
            retained_synthesized_output_justification=_SYNTH_OUTPUT_JUSTIFICATION,
            retained_audit_trail_justification=_AUDIT_TRAIL_JUSTIFICATION,
        )
