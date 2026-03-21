"""Data retention cleanup logic for the synthesis module.

This module implements the retention policy defined in T41.1:

- Deletes ``synthesis_job`` records older than the configured TTL.
- Never deletes jobs under legal hold (``legal_hold=True``).
- Removes associated Parquet artifact files for deleted jobs.
- Emits a ``JOB_RETENTION_PURGE`` WORM audit event per deletion.
- Never touches audit event records — audit events are append-only
  and handled at the infrastructure/log-shipping layer.

Boundary constraints (import-linter enforced):
    - Must NOT import from ``modules/ingestion/``, ``modules/masking/``,
      ``modules/subsetting/``, ``modules/profiler/``, or ``modules/privacy/``.
    - Must NOT import from ``bootstrapper/``.

CONSTITUTION Priority 0: Security — deletions audited, PII-free log entries
CONSTITUTION Priority 5: Code Quality — strict typing, Google docstrings
Task: T41.1 — Implement Data Retention Policy
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import Engine
from sqlmodel import Session, col, select

from synth_engine.modules.synthesizer.job_models import SynthesisJob
from synth_engine.shared.security.audit import get_audit_logger

_logger = logging.getLogger(__name__)


class RetentionCleanup:
    """Performs scheduled data retention cleanup for synthesis job records.

    Deletes expired, non-held ``synthesis_job`` records from the database
    and removes their associated artifact files from the filesystem.  Every
    deletion is logged to the WORM audit trail so purges are detectable and
    attributable.

    Audit events are NOT affected by this class — the audit trail is
    append-only during the configured retention period and must only be
    archived (never deleted) via out-of-band cold-storage tooling.

    Args:
        engine: SQLAlchemy engine to use for database access.
        job_retention_days: Number of days after which a job record is
            eligible for deletion (unless ``legal_hold=True``).
    """

    def __init__(self, *, engine: Engine, job_retention_days: int) -> None:
        self._engine = engine
        self._job_retention_days = job_retention_days

    def cleanup_expired_jobs(self) -> int:
        """Delete synthesis job records older than the configured TTL.

        Identifies all ``SynthesisJob`` records whose ``created_at``
        timestamp is older than ``job_retention_days`` days and whose
        ``legal_hold`` flag is ``False``.  For each eligible record:

        1. Deletes associated artifact files from the filesystem (best-effort;
           missing files are silently ignored).
        2. Deletes the database record.
        3. Emits a ``JOB_RETENTION_PURGE`` audit event.

        Audit events are never touched by this method.

        Returns:
            The number of job records deleted.
        """
        cutoff: datetime = datetime.now(UTC) - timedelta(days=self._job_retention_days)
        deleted_count: int = 0

        with Session(self._engine) as session:
            expired_jobs = session.exec(
                select(SynthesisJob)
                .where(col(SynthesisJob.created_at) < cutoff)
                .where(SynthesisJob.legal_hold == False)  # noqa: E712
            ).all()

            for job in expired_jobs:
                # Best-effort artifact removal — missing files are not errors.
                self._delete_artifact(job)

                job_id = job.id
                table_name = job.table_name

                session.delete(job)
                session.commit()

                _logger.info(
                    "Retention purge: deleted synthesis_job id=%s table=%s",
                    job_id,
                    table_name,
                )

                # Emit WORM audit event per deletion (T41.1 AC5).
                # Details contain only non-PII metadata (job_id, table_name).
                get_audit_logger().log_event(
                    event_type="JOB_RETENTION_PURGE",
                    actor="system/retention",
                    resource=f"synthesis_job/{job_id}",
                    action="delete",
                    details={
                        "job_id": str(job_id),
                        "table_name": table_name,
                        "retention_days": str(self._job_retention_days),
                    },
                )

                deleted_count += 1

        return deleted_count

    def _delete_artifact(self, job: SynthesisJob) -> None:
        """Remove a job's output artifact file from the filesystem.

        This is a best-effort operation.  If the file does not exist or
        cannot be removed due to a permissions error, a WARNING is logged
        but no exception is raised.

        Args:
            job: The :class:`SynthesisJob` whose artifact should be removed.
        """
        if not job.output_path:
            return

        artifact = Path(job.output_path)
        try:
            artifact.unlink(missing_ok=True)
            _logger.debug("Deleted artifact: %s", artifact.name)
        except OSError as e:
            _logger.warning(
                "Could not delete artifact for job id=%s: %s",
                job.id,
                type(e).__name__,
            )
