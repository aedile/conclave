"""Data retention cleanup logic for the synthesis module.

This module implements the retention policy defined in T41.1 and ADR-D3:

- Deletes ``synthesis_job`` records older than the configured TTL.
- Only deletes jobs in terminal states (COMPLETE or FAILED) — in-flight jobs
  (QUEUED, TRAINING, GENERATING) are NEVER deleted, even if older than the TTL.
- Never deletes jobs under legal hold (``legal_hold=True``).
- Removes associated Parquet artifact files for jobs older than
  ``artifact_retention_days`` (independent sweep).
- Emits a ``JOB_RETENTION_PURGE`` or ``ARTIFACT_RETENTION_PURGE`` WORM audit
  event per deletion.  Audit logging is best-effort — a logging failure will
  not prevent the deletion from completing.
- Error isolation: a DB commit failure on one job is caught and logged; the
  loop continues to the next job rather than aborting.
- Never touches audit event records — audit events are append-only and handled
  at the infrastructure/log-shipping layer.

Boundary constraints (import-linter enforced):
    - Must NOT import from ``modules/ingestion/``, ``modules/masking/``,
      ``modules/subsetting/``, ``modules/profiler/``, or ``modules/privacy/``.
    - Must NOT import from ``bootstrapper/``.

CONSTITUTION Priority 0: Security — deletions audited, PII-free log entries
CONSTITUTION Priority 5: Code Quality — strict typing, Google docstrings
Task: T41.1 — Implement Data Retention Policy
Task: ADR-D3 — Wire Retention Cleanup to Huey Periodic Task (ADV-019/020)
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session, col, select

from synth_engine.modules.synthesizer.jobs.job_models import SynthesisJob
from synth_engine.shared.security.audit import get_audit_logger

_logger = logging.getLogger(__name__)

#: Terminal job statuses eligible for routine retention purge.
#: In-flight statuses (QUEUED, TRAINING, GENERATING) are intentionally excluded —
#: deleting an active job mid-training would corrupt the training pipeline.
_TERMINAL_STATUSES: frozenset[str] = frozenset({"COMPLETE", "FAILED"})


class RetentionCleanup:
    """Performs scheduled data retention cleanup for synthesis job records.

    Deletes expired, non-held ``synthesis_job`` records from the database
    (only those in terminal states) and removes their associated artifact
    files from the filesystem.  Every deletion is logged to the WORM audit
    trail so purges are detectable and attributable.  Errors on individual
    jobs are isolated — a single failure does not abort the loop.

    Audit events are NOT affected by this class — the audit trail is
    append-only during the configured retention period and must only be
    archived (never deleted) via out-of-band cold-storage tooling.

    Args:
        engine: SQLAlchemy engine to use for database access.
        job_retention_days: Number of days after which a job record is
            eligible for deletion (unless ``legal_hold=True`` or the job is
            in a non-terminal status).
        artifact_retention_days: Number of days after which a job's output
            artifact file is eligible for an independent disk sweep.  Defaults
            to ``None`` (artifact sweep disabled unless explicitly provided).
    """

    def __init__(
        self,
        *,
        engine: Engine,
        job_retention_days: int,
        artifact_retention_days: int | None = None,
    ) -> None:
        self._engine = engine
        self._job_retention_days = job_retention_days
        self._artifact_retention_days = artifact_retention_days

    def cleanup_expired_jobs(self) -> int:
        """Delete synthesis job records older than the configured TTL.

        Identifies all ``SynthesisJob`` records whose ``created_at``
        timestamp is older than ``job_retention_days`` days, whose
        ``legal_hold`` flag is ``False``, and whose ``status`` is in
        ``{COMPLETE, FAILED}`` (terminal states only).

        For each eligible record:

        1. Deletes associated artifact files from the filesystem (best-effort;
           missing files are silently ignored).
        2. Deletes the database record.
        3. Emits a ``JOB_RETENTION_PURGE`` audit event (best-effort — audit
           failure does not prevent the deletion).

        In-flight jobs (QUEUED, TRAINING, GENERATING) are NEVER deleted,
        even if their ``created_at`` predates the cutoff.  Audit events are
        never touched by this method.

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
                .where(SynthesisJob.status.in_(list(_TERMINAL_STATUSES)))  # type: ignore[attr-defined]
            ).all()

            for job in expired_jobs:
                job_id = job.id
                table_name = job.table_name

                try:
                    # Best-effort artifact removal — missing files are not errors.
                    self._delete_artifact(job)

                    session.delete(job)
                    session.commit()
                except (OSError, SQLAlchemyError) as exc:
                    # OSError: artifact file deletion failure (permissions, disk I/O).
                    # SQLAlchemyError: DB commit failure (deadlock, constraint violation).
                    # Any other exception is a programming error and must propagate.
                    _logger.warning(
                        "Retention purge: failed to delete job id=%s table=%s — %s: %s",
                        job_id,
                        table_name,
                        type(exc).__name__,
                        exc,
                    )
                    session.rollback()
                    continue

                _logger.info(
                    "Retention purge: deleted synthesis_job id=%s table=%s",
                    job_id,
                    table_name,
                )

                # Emit WORM audit event per deletion (T41.1 AC5) — best-effort.
                # Details contain only non-PII metadata (job_id, table_name).
                try:
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
                except Exception as audit_exc:
                    _logger.warning(
                        "Retention purge: audit log failed for job id=%s — %s",
                        job_id,
                        type(audit_exc).__name__,
                    )

                deleted_count += 1

        return deleted_count

    def cleanup_expired_artifacts(self) -> int:
        """Sweep artifact files for jobs older than ``artifact_retention_days``.

        Queries ``SynthesisJob`` records whose ``created_at`` predates the
        artifact cutoff, whose ``output_path`` is not NULL, whose
        ``legal_hold`` is ``False``, and whose ``status`` is terminal
        (COMPLETE or FAILED).

        For each eligible record:

        1. Deletes the artifact file from disk (best-effort; missing files
           are silently ignored).
        2. Sets ``output_path = None`` on the job record and commits.
        3. Emits an ``ARTIFACT_RETENTION_PURGE`` audit event (best-effort).

        Raises:
            RuntimeError: If called when ``artifact_retention_days`` was not
                provided at construction time.

        Returns:
            The number of artifact files swept.
        """
        if self._artifact_retention_days is None:
            raise RuntimeError(
                "cleanup_expired_artifacts() requires artifact_retention_days to be set "
                "at RetentionCleanup construction time."
            )

        cutoff: datetime = datetime.now(UTC) - timedelta(days=self._artifact_retention_days)
        swept_count: int = 0

        with Session(self._engine) as session:
            candidates = session.exec(
                select(SynthesisJob)
                .where(col(SynthesisJob.created_at) < cutoff)
                .where(SynthesisJob.legal_hold == False)  # noqa: E712
                .where(SynthesisJob.status.in_(list(_TERMINAL_STATUSES)))  # type: ignore[attr-defined]
                .where(col(SynthesisJob.output_path).isnot(None))
            ).all()

            for job in candidates:
                job_id = job.id
                artifact_path = job.output_path

                try:
                    if artifact_path:
                        Path(artifact_path).unlink(missing_ok=True)
                        _logger.debug("Swept artifact: %s", artifact_path)

                    job.output_path = None
                    session.add(job)
                    session.commit()
                except (OSError, SQLAlchemyError) as exc:
                    # OSError: artifact file deletion failure (permissions, disk I/O).
                    # SQLAlchemyError: DB commit failure (deadlock, constraint violation).
                    # Any other exception is a programming error and must propagate.
                    _logger.warning(
                        "Artifact sweep: failed for job id=%s — %s: %s",
                        job_id,
                        type(exc).__name__,
                        exc,
                    )
                    session.rollback()
                    continue

                _logger.info(
                    "Artifact sweep: cleared output_path for job id=%s",
                    job_id,
                )

                # Best-effort audit event.
                try:
                    get_audit_logger().log_event(
                        event_type="ARTIFACT_RETENTION_PURGE",
                        actor="system/retention",
                        resource=f"synthesis_job/{job_id}/artifact",
                        action="delete",
                        details={
                            "job_id": str(job_id),
                            "artifact_retention_days": str(self._artifact_retention_days),
                        },
                    )
                except Exception as audit_exc:
                    _logger.warning(
                        "Artifact sweep: audit log failed for job id=%s — %s",
                        job_id,
                        type(audit_exc).__name__,
                    )

                swept_count += 1

        return swept_count

    def _delete_artifact(self, job: SynthesisJob) -> None:
        """Remove a job's output artifact file from the filesystem.

        This is a best-effort operation.  If the file does not exist it is
        silently ignored (missing_ok=True).  If the file cannot be removed
        due to an OS error (e.g. permissions), a WARNING is logged but no
        exception is raised.

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
