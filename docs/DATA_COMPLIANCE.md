# Conclave Engine — Data Compliance & Retention Policy

This document describes Conclave's data handling practices, retention policies,
and compliance mechanisms for GDPR (Articles 5, 17, 30), CCPA, and HIPAA-adjacent
deployments. It is the authoritative reference for compliance officers, auditors,
and operators who need to understand how the engine processes, retains, and
destroys data.

For day-to-day operations, see [docs/OPERATOR_MANUAL.md](OPERATOR_MANUAL.md).
For security controls, see [docs/infrastructure_security.md](infrastructure_security.md).
For the retention architecture decision, see [docs/adr/ADR-0041-data-retention-compliance.md](adr/ADR-0041-data-retention-compliance.md).

---

## 1. Data Categories and Retention Periods

Conclave processes three distinct categories of data. Each category has a
different retention lifecycle, protection requirement, and deletion mechanism.

| Category | What it contains | Default retention | Deletion mechanism |
|----------|-----------------|-------------------|--------------------|
| **Job records** | Synthesis job metadata, status, epsilon spent, error messages | 90 days (`JOB_RETENTION_DAYS`) | Automated purge task |
| **Audit events** | WORM cryptographic audit trail of all security operations | 1,095 days / 3 years (`AUDIT_RETENTION_DAYS`) | Archive to cold storage; never deleted within retention period |
| **Synthesis artifacts** | Parquet output files, model checkpoints on MinIO tmpfs | 30 days (`ARTIFACT_RETENTION_DAYS`); immediately on container restart | NIST SP 800-88 shred or tmpfs reclaim |

Retention periods are configurable via `ConclaveSettings` environment variables.
See [Section 5](#5-retention-configuration) for configuration details.

---

## 2. What PII is Processed and How

Conclave connects read-only to a source PostgreSQL database. It never writes to
the source. The data it processes follows this pipeline:

```
Source DB (real PII) → Ingestion → Schema Reflection → [Mode selector]
                                                              |
                       ┌─────────────────────────────────────┤
                       ↓                                     ↓
              Deterministic Masking                  Statistical Profiling
              (HMAC-SHA256 seeded Faker)             (histograms, covariances)
                       |                                     |
                       |                             DP-CTGAN Training (DP-SGD)
                       |                                     |
                       |                             Privacy Budget Accounting
                       |                                     |
                       └─────────────→ Saga Egress Writer ←──┘
                                              |
                                        Target DB / Parquet artifact
                                        (no real PII)
```

### 2.1 Read-Only Ingestion

The source database connection is validated for read-only access at connection
time. If the credential has write privileges, the job is refused before any
data is read. PII leaves the source database in memory only — it is never
written to disk in raw form.

### 2.2 Deterministic Masking (Mask Mode)

In masking mode, PII columns are replaced with HMAC-SHA256 seeded Faker values.
The masking function is:

```
masked_value = Faker(category).generate(seed=HMAC-SHA256(masking_salt || real_value))
```

Properties:

- The same real value always produces the same masked value (referential
  integrity across tables is preserved).
- The transformation is not reversible without the `MASKING_SALT` secret.
- Masked output contains no real PII.

### 2.3 Differential Privacy Synthesis (Synthesize Mode)

In synthesis mode, Conclave trains a CTGAN generative model using Opacus
DP-SGD (discriminator-level, per ADR-0036). The output is statistically similar
to the source data but contains no real records.

Formal guarantees:

- **(epsilon, delta)-differential privacy**: The trained model satisfies
  (ε, δ = 1e-5)-DP. Any single source record's influence on the model is
  bounded by ε.
- **No memorization**: The Generator is trained on Discriminator outputs, not
  directly on real data. The Discriminator's gradient updates are privatized
  by DP-SGD before influencing the Generator.
- **Epsilon budget enforcement**: The `spend_budget` function in
  `modules/privacy/accountant.py` raises `BudgetExhaustionError` if the job
  would exceed the configured per-ledger epsilon budget. Budget spending is
  atomic (pessimistic `SELECT ... FOR UPDATE` locking prevents concurrent
  jobs from overrunning the budget).

Synthesized output is not PII under GDPR Recital 26 or CCPA § 1798.140(v)
because: (a) it is generated data with formal DP guarantees, (b) it cannot be
re-identified to a specific individual with reasonable effort, and (c) it
shares no exact values with the source dataset.

### 2.4 What Leaves the Air Gap

Nothing. The source data, intermediate artifacts, and synthetic output all
remain within the operator's deployment perimeter. Conclave makes no external
network calls. There is no telemetry, license call-home, or model registry
upload.

---

## 3. Right-to-Erasure Procedure (GDPR Article 17 / CCPA § 1798.105)

### 3.1 What the Erasure Endpoint Deletes

The `DELETE /compliance/erasure` endpoint accepts a data subject identifier
and cascades deletion through:

| Record type | Action | Justification |
|-------------|--------|---------------|
| Source connection metadata referencing the subject | Deleted | Direct PII reference |
| Job records that processed the subject's data | Deleted | Indirect association |
| Synthesized output in target DB | **Preserved** | DP-protected; not attributable to any individual |
| Audit trail entries | **Preserved** | Required for compliance proof; deletion would compromise the audit |
| WORM hash chain | **Preserved** | Deletion would break cryptographic integrity |

The erasure endpoint returns a compliance receipt documenting every record
deleted and every record preserved, with a written justification for each
preservation decision. This receipt is the operator's evidence of compliance
with an erasure request.

The endpoint is implemented in `src/synth_engine/bootstrapper/routers/compliance.py`
and `src/synth_engine/modules/synthesizer/erasure.py`. See ADR-0041 for the
architectural rationale.

### 3.2 Preservation Justifications

**Synthesized output is preserved** because differentially private synthetic
data does not constitute personal data under GDPR Recital 26. The DP guarantee
means no individual's contribution to the output is statistically detectable.
Deleting it would provide no additional privacy protection while destroying
legitimate derivative work product.

**Audit trail entries are preserved** because Article 17(3)(b) exempts deletion
where retention is necessary for compliance with a legal obligation, and Article
17(3)(e) exempts deletion where the data is necessary for the establishment,
exercise, or defence of legal claims. The audit trail is both: it proves the
operator processed data lawfully and can demonstrate erasure compliance. The
audit trail retention period (`AUDIT_RETENTION_DAYS`) is set to satisfy this
requirement.

### 3.3 Vault-Sealed State

If the vault is sealed when an erasure request is received, the endpoint returns
`423 Locked`. ALE-encrypted records cannot be identified for deletion without
the KEK derived at unseal time. The operator must unseal the vault before
processing erasure requests.

### 3.4 Erasure Logging

The erasure request itself — who requested it, when, how many records were
deleted, and what was preserved — is logged to the WORM audit trail. The
subject identifier is never written into the audit event payload (CONSTITUTION
Priority 0: no PII in audit payloads). The audit entry cannot be deleted and
forms the durable compliance proof.

---

## 4. Audit Trail Immutability Guarantees

Conclave's audit log is a WORM (Write-Once, Read-Many) append-only trail.

### 4.1 Cryptographic Integrity

Each audit event is HMAC-SHA256 signed with `AUDIT_KEY` before being appended.
The signature covers the event payload, timestamp, and a sequence number. Any
tampering — modification, deletion, or reordering of events — is detectable by
recomputing the HMAC chain.

### 4.2 Append-Only Enforcement

The `AuditLogger` class accepts only `log_event()` calls. There are no
`delete()`, `update()`, or `truncate()` methods. The application code has no
path to delete an audit event during the retention period. This is enforced
by the module boundary: the audit logger is in `shared/` and exposes only an
append interface.

### 4.3 Retention Floor

Audit events are never deleted within `AUDIT_RETENTION_DAYS` (default 3 years).
The automated purge task explicitly excludes the audit table. After the retention
period, events may be archived to cold storage but must not be permanently
deleted until the operator confirms they are no longer required for open
investigations or legal holds.

---

## 5. Retention Configuration

Retention periods are configured via `ConclaveSettings` fields, which map
directly to environment variables.

| Setting | Environment variable | Default | Description |
|---------|---------------------|---------|-------------|
| `job_retention_days` | `JOB_RETENTION_DAYS` | `90` | Days before completed/failed synthesis jobs are purged |
| `audit_retention_days` | `AUDIT_RETENTION_DAYS` | `1095` | Days audit events are retained before archival eligibility |
| `artifact_retention_days` | `ARTIFACT_RETENTION_DAYS` | `30` | Days Parquet artifacts and model checkpoints are retained |

Set these in `.env` or inject them as environment variables:

```bash
# .env — compliance retention overrides
JOB_RETENTION_DAYS=180          # 6 months for financial compliance
AUDIT_RETENTION_DAYS=2555       # 7 years for financial-sector requirements
ARTIFACT_RETENTION_DAYS=14      # 2-week artifact window for sensitive deployments
```

Operator configuration instructions are in
[docs/OPERATOR_MANUAL.md — Section 11](OPERATOR_MANUAL.md).

### 5.1 Retention Period Guidance by Regulatory Context

| Regulatory context | Recommended `AUDIT_RETENTION_DAYS` | Basis |
|--------------------|------------------------------------|-------|
| GDPR (EU) | 1,095 (3 years) | GDPR Article 5(1)(e) storage limitation; legal claims window |
| CCPA (California) | 1,095 (3 years) | CCPA § 1798.100(e) |
| HIPAA (US healthcare) | 2,190 (6 years) | 45 CFR § 164.530(j) |
| Financial services (SEC Rule 17a-4) | 2,555 (7 years) | SEC Rule 17a-4(b)(1) |

These are starting points. Consult your legal counsel for your specific
jurisdiction and data category.

---

## 6. Legal Hold Mechanism

A `legal_hold` boolean flag on `SynthesisJob` records prevents deletion
regardless of the configured TTL. This satisfies e-discovery obligations and
regulatory hold requirements.

### 6.1 Setting a Legal Hold

Legal holds are set via the admin API (requires operator authentication):

```bash
# Toggle legal hold for a job (enable: true sets hold, enable: false releases it)
curl -X PATCH http://<host>:8000/admin/jobs/<job-id>/legal-hold \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"enable": true}'

# Release a legal hold
curl -X PATCH http://<host>:8000/admin/jobs/<job-id>/legal-hold \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"enable": false}'
```

Every hold and release operation is logged to the WORM audit trail with the
requesting operator's identity and timestamp.

### 6.2 Hold Semantics

- A job on legal hold is skipped by the automated purge task.
- A job on legal hold cannot be deleted via `DELETE /jobs/<id>`.
- Legal holds survive vault reseal/unseal cycles.
- Releasing a legal hold does not immediately delete the job — it restores
  eligibility for routine purge at the next purge cycle.

---

## 7. Data Flow Diagram

```
┌─────────────────────────────────────────────────────────────────────┐
│  SOURCE (operator's perimeter)                                       │
│                                                                      │
│  Source PostgreSQL                                                   │
│     │  read-only connection, pre-flight privilege check              │
│     ↓                                                                │
│  Ingestion Module (modules/ingestion/)                               │
│     │  schema reflection, FK DAG, topological sort                   │
│     ↓                                                                │
│  FK-Aware Subsetting (modules/subsetting/)                           │
│     │  surgically precise extraction, no orphan rows                 │
│     ↓                                                                │
│  ┌──────────────────────┬──────────────────────────────────────┐     │
│  │  MASK MODE           │  SYNTHESIZE MODE                     │     │
│  │                      │                                      │     │
│  │  Masking Module      │  Profiler Module                     │     │
│  │  (modules/masking/)  │  (modules/profiler/)                 │     │
│  │  HMAC-SHA256 seeded  │  histograms, covariances             │     │
│  │  Faker substitution  │           ↓                          │     │
│  │  not reversible      │  Synthesizer Module                  │     │
│  │  PII→ fake-PII       │  (modules/synthesizer/)              │     │
│  │                      │  DP-CTGAN + Opacus DP-SGD            │     │
│  │                      │  epsilon/delta accounting            │     │
│  │                      │  PII→ synthetic (no real records)    │     │
│  └──────────┬───────────┴────────────────┬─────────────────────┘     │
│             │                            │                           │
│             └───────────┬────────────────┘                           │
│                         ↓                                            │
│  Saga-Pattern Egress Writer (modules/subsetting/)                    │
│     │  transactional write; rollback on failure                      │
│     ↓                                                                │
│  Target PostgreSQL  /  Parquet artifact (MinIO tmpfs)                │
│                         no real PII in output                        │
│                                                                      │
│  ┌────────────────────────────────────────────────────────────────┐  │
│  │  AUDIT TRAIL (shared/security/audit.py)                          │  │
│  │  WORM, HMAC-signed, append-only; records every job lifecycle   │  │
│  │  event, vault operation, erasure request, and legal hold.      │  │
│  └────────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────┘
```

All processing occurs within the operator's deployment perimeter.
No data leaves the network boundary at any stage.

---

## 8. Automated Purge Task

The purge task is wired to Huey `@periodic_task` cron jobs in
`bootstrapper/retention_tasks.py` (wired in advisory-drain-pre-p44). The retention
policy is fully operational. See ADR-0041 for implementation details.

The purge task runs as a scheduled Huey job. It performs the following actions
on each execution:

1. Query `synthesis_job` for records where `created_at < NOW() - job_retention_days`
   AND `legal_hold = false` AND `status IN ('COMPLETE', 'FAILED', 'SHREDDED')`.
2. For each eligible job, shred its artifacts (NIST SP 800-88) and delete the
   job record.
3. Query MinIO for Parquet artifacts older than `artifact_retention_days` that
   are not associated with a legal-held job.
4. Delete eligible artifacts.
5. Log a purge summary to the WORM audit trail (count of jobs deleted, count
   of artifacts deleted, any errors encountered).
6. Never touch audit events.

The purge task is configured in `src/synth_engine/modules/synthesizer/retention.py`
and wired to the Huey scheduler in the bootstrapper.

---

## 9. Compliance Certifications and Attestations

Conclave provides the following compliance-relevant properties out of the box.
Operators are responsible for ensuring their deployment configuration and
operational practices satisfy the full requirements of any applicable regulation.

| Property | Implementation | Evidence |
|----------|---------------|---------|
| Data minimization (GDPR Art. 5(1)(c)) | Read-only ingestion; source data never persisted to disk in raw form | Pre-flight privilege check; ingestion module design |
| Storage limitation (GDPR Art. 5(1)(e)) | Configurable retention TTLs; automated purge task (scheduler wiring deferred) | `ConclaveSettings` retention fields; `modules/synthesizer/retention.py` |
| Right to erasure (GDPR Art. 17) | `DELETE /compliance/erasure` endpoint with cascade deletion and compliance receipt | `modules/synthesizer/erasure.py`; `bootstrapper/routers/compliance.py`; audit log entry per request |
| Audit trail integrity | WORM, HMAC-SHA256 signed, append-only; no delete path in application code | `shared/security/audit.py`; WORM module design |
| Formal privacy guarantee | (ε, δ)-DP on synthesized output via Opacus DP-SGD | `spend_budget` / `reset_budget` in `modules/privacy/accountant.py`; ADR-0036 |
| Air-gap compliance | No external network calls; offline license activation | Network isolation design; `make build-airgap-bundle` |
| Cryptographic erasure | NIST SP 800-88 compliant shredding of synthesis artifacts | `modules/synthesizer/shred.py`; ADR-0034 |
| Legal hold | `legal_hold` boolean on job records; prevents purge | `PATCH /admin/jobs/{id}/legal-hold`; `bootstrapper/routers/admin.py`; `modules/synthesizer/retention.py` |

---

## 10. Operator Responsibilities

Conclave provides the technical mechanisms. Operators must:

1. **Configure retention periods** appropriate for their regulatory context
   before going to production (see [Section 5](#5-retention-configuration)).
2. **Schedule the purge task** to run at an appropriate cadence
   (daily recommended). Note: scheduler wiring is deferred — see Section 8.
3. **Protect `AUDIT_KEY`** and rotate it per the key rotation procedures in
   [docs/OPERATOR_MANUAL.md](OPERATOR_MANUAL.md). Loss of `AUDIT_KEY` renders
   the audit trail unverifiable.
4. **Maintain a data processing record** (GDPR Article 30) documenting the
   categories of source data processed, processing purposes, and data subject
   categories. Conclave's audit trail provides the technical evidence to
   support this record but does not replace it.
5. **Process erasure requests promptly**. GDPR Article 12(3) requires
   erasure within one calendar month of receipt. Use `DELETE /compliance/erasure`
   to submit erasure requests programmatically.
6. **Test the erasure endpoint** before production deployment to verify
   cascade deletion behaves as expected for your schema.
