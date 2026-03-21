# ADR-0041: Data Retention and Compliance Architecture

**Status**: Accepted
**Date**: 2026-03-21
**Task**: T41.1 — Data Retention Policy
**Deciders**: Engineering team

---

## Context

Prior to T41.1, `synthesis_job` records were retained indefinitely in the database.
There was no TTL-based purge cycle, no mechanism to prevent cleanup of records under
active investigation, and no audit trail for deletions.

This created three compliance risks:

1. **Storage sprawl**: Synthesis jobs accumulate without bound. In long-running deployments
   artifact files (output CSVs, Parquet) also pile up on disk, consuming storage without
   business justification.

2. **Regulatory non-compliance**: Data minimisation principles (GDPR Article 5(1)(e),
   HIPAA minimum-necessary) require that records not be retained longer than necessary
   for their stated purpose. Indefinite retention is indefensible under audit.

3. **Legal hold gap**: Investigation or litigation scenarios require the ability to exempt
   specific records from automatic deletion. Without a legal hold mechanism, an automated
   cleanup job could destroy evidence that legal or compliance teams need to preserve.

---

## Decision

Implement **configurable TTL-based retention** with a **legal hold override** and an
**immutable audit trail** for every deletion event.

### 1. Three-tier retention configuration (shared/settings.py)

Three environment variables control retention windows:

| Variable | Default | Governs |
|----------|---------|---------|
| `JOB_RETENTION_DAYS` | `90` | `synthesis_job` row lifetime |
| `AUDIT_RETENTION_DAYS` | `1095` | Audit event row lifetime |
| `ARTIFACT_RETENTION_DAYS` | `30` | Output files on disk |

Defaults reflect a conservative baseline that is defensible under GDPR and common
enterprise data governance policies. Operators may tighten or relax each window
independently via environment configuration — no code changes required.

Audit events carry a longer default (1095 days, 3 years) than synthesis jobs (90 days)
because audit logs are the evidentiary record for compliance reviews. Deleting audit logs
on the same schedule as the records they describe would undermine their value.

### 2. Legal hold field on SynthesisJob (job_models.py)

`SynthesisJob.legal_hold` is a non-nullable boolean (`server_default=False`, `index=True`).

Any job with `legal_hold=True` is unconditionally skipped by the cleanup sweep, regardless
of age. This is the structural guarantee: the retention cleaner inspects the flag and
short-circuits before any deletion attempt. No configuration value overrides a legal hold.

The field is indexed because the cleanup query filters on it (`WHERE legal_hold = FALSE`)
in combination with `created_at`. An unindexed scan over a large `synthesis_job` table
on every scheduled run would be unacceptable.

### 3. RetentionCleanup (modules/synthesizer/retention.py)

A single-responsibility class with one public method: `cleanup_expired_jobs()`.

The cleanup algorithm:

1. Compute cutoff timestamp as `now() - timedelta(days=job_retention_days)`.
2. Query `synthesis_job` WHERE `created_at < cutoff AND legal_hold = FALSE`.
3. For each expired job: attempt to delete the artifact file, then delete the DB row.
4. Emit one structured audit event per deleted job (see audit event format below).
5. Suppress missing-file errors on artifact deletion — artifact may have already been
   purged by a prior run or an external process. Missing files are silently ignored via
   `missing_ok=True`; this is not an error condition.

Audit events are emitted only when at least one row is deleted, avoiding noise in the
audit log during routine runs when no jobs have expired.

The class creates its own database session internally; callers do not pass a session.

The class is placed in `modules/synthesizer/` because it exclusively operates on
`SynthesisJob` domain objects and their associated artifacts. It is not a cross-cutting
concern — it has no reason to exist in `shared/`.

### 4. Legal hold endpoint (bootstrapper/routers/admin.py)

`PATCH /admin/jobs/{job_id}/legal-hold` accepts a JSON body `{"enable": true|false}` and
sets `SynthesisJob.legal_hold` accordingly.

Design rationale:

- **Admin router separation**: Legal hold is an administrative action distinct from normal
  job lifecycle operations. A dedicated `admin` router makes it easy to apply role-based
  access control (RBAC) at the router level in a future task without retrofitting the
  existing jobs router.
- **PATCH semantics**: PATCH models a partial update to the job resource (toggling the
  `legal_hold` field) which aligns with RFC 5789 semantics.
- **404 on missing job**: Returns HTTP 404 when the job ID does not exist. Consistent with
  the IDOR pattern established in ADR-0040.

### 5. Audit event on deletion

Every deleted job produces an audit event written via `AuditLogger` with:

- `event_type`: `"JOB_RETENTION_PURGE"`
- `actor`: `"system/retention"`
- `resource`: `f"synthesis_job/{job_id}"` (where `job_id` is the integer primary key)
- `action`: `"delete"`
- `details`: `{"job_id": str(job_id), "table_name": table_name, "retention_days": str(retention_days)}`

This provides a complete deletion history that survives the deletion of the job row itself.
Audit events are governed by `AUDIT_RETENTION_DAYS` (default 1095), giving compliance
teams a three-year window to retrieve deletion records.

---

## Known Debt

### Scheduler wiring — Resolved (advisory-drain-pre-p44)

`RetentionCleanup.cleanup_expired_jobs()` and `cleanup_expired_artifacts()` are wired
to Huey `@periodic_task` cron jobs in `bootstrapper/retention_tasks.py`:

- `cleanup_expired_jobs`: runs daily at 02:00 UTC
- `cleanup_expired_artifacts`: runs daily at 03:00 UTC

The BLOCKER advisory (ADV-019/ADV-020) raised per Rule 8 in T41.1 was drained in
the advisory-drain-pre-p44 branch. The retention policy is fully operational.

### Artifact retention window not enforced on disk independently

Currently, artifact file deletion is coupled to job row deletion: when a job row is
deleted, its artifact file is also removed. `ARTIFACT_RETENTION_DAYS` configures the
same TTL for both.

A more precise implementation would sweep disk independently of the DB, using
`ARTIFACT_RETENTION_DAYS` to purge files from expired jobs whose rows may have already
been deleted. This decoupling is deferred as non-critical for the current deployment
model where job rows and artifacts are co-located.

---

## Consequences

### Positive

- Synthesis job records are automatically purged after a configurable TTL, eliminating
  indefinite storage accumulation.
- Legal hold provides a structural guarantee that no record under investigation is
  automatically deleted, regardless of age.
- Every deletion is audited with a structured event, providing a compliance-grade
  deletion history.
- All three retention windows are independently configurable via environment variables,
  requiring no code changes to adjust retention policy.
- The admin router structure prepares for future RBAC enforcement on legal hold operations.

### Negative / Trade-offs

- **Scheduler wiring deferred**: The cleanup mechanism has no effect until wired to a
  scheduler. This is a known gap documented as an advisory.
- **Legal hold is binary**: There is no time-bound hold or hold expiry. A hold must be
  manually released via the API. This is acceptable for the MVP; automated hold expiry
  can be added when litigation workflow requirements are clearer.
- **Legal hold changes are audited**: Setting or clearing a legal hold emits `LEGAL_HOLD_SET`
  and `LEGAL_HOLD_CLEARED` audit events respectively, providing a full trail of hold
  lifecycle changes.

---

## Alternatives Considered

| Option | Rejected Because |
|--------|-----------------|
| Soft-delete (deleted_at column) | Does not reduce storage; records still accumulate. Requires all queries to filter `deleted_at IS NULL`. Adds ongoing query complexity without the compliance benefit of actual deletion. |
| Database-level partitioning and partition drop | Requires PostgreSQL partition management, which is ops-heavy and not portable to the SQLite test environment. TTL-based cleanup achieves the same outcome with far less complexity. |
| Single retention window for all record types | Audit logs have different regulatory retention requirements than operational records. A single window would force operators to choose between over-retaining operational records or under-retaining audit evidence. Three independent windows is the correct model. |
| Hard-coded retention periods | Environment variable configuration is required for air-gapped deployments where different customers have different regulatory obligations. Hard-coded periods are not acceptable for a multi-customer product. |

---

## References

- GDPR Article 5(1)(e) — Storage limitation principle
- HIPAA 45 CFR 164.530(j) — Documentation and record retention
- ADR-0040 — IDOR Protection (establishes 404 pattern for missing resources)
- ADR-0039 — JWT Bearer Token Authentication (auth context for admin endpoint)
- `src/synth_engine/modules/synthesizer/retention.py` — RetentionCleanup
- `src/synth_engine/bootstrapper/routers/admin.py` — Legal hold endpoint
- `src/synth_engine/shared/settings.py` — RetentionSettings
- `src/synth_engine/modules/synthesizer/job_models.py` — SynthesisJob.legal_hold
