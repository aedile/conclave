# Conclave Engine — Data Compliance & Retention Policy

Authoritative reference for compliance officers, auditors, and operators. Covers GDPR (Articles 5, 17, 30), CCPA, and HIPAA-adjacent deployments.

- Day-to-day operations: [OPERATOR_MANUAL.md](OPERATOR_MANUAL.md)
- Security controls: [infrastructure_security.md](infrastructure_security.md)
- Retention architecture decision: [ADR-0041](adr/ADR-0041-data-retention-compliance.md)

---

## 1. Data Categories and Retention Periods

| Category | Contents | Default retention | Deletion mechanism |
|----------|---------|-------------------|--------------------|
| **Job records** | Synthesis job metadata, status, epsilon spent, error messages | 90 days (`JOB_RETENTION_DAYS`) | Automated purge task |
| **Audit events** | WORM cryptographic audit trail of all security operations | 1,095 days / 3 years (`AUDIT_RETENTION_DAYS`) | Archive to cold storage; never deleted within retention period |
| **Synthesis artifacts** | Parquet output files, model checkpoints on MinIO tmpfs | 30 days (`ARTIFACT_RETENTION_DAYS`); immediately on container restart | NIST SP 800-88 shred or tmpfs reclaim |

Retention periods are configurable via `ConclaveSettings` environment variables. See [Section 5](#5-retention-configuration).

---

## 2. What PII is Processed and How

Conclave connects read-only to a source PostgreSQL database and never writes to the source.

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

The source connection is validated for read-only access at connection time. Write-privileged credentials cause the job to be refused before any data is read. PII travels in memory only — never written to disk in raw form.

### 2.2 Deterministic Masking (Mask Mode)

PII columns are replaced with HMAC-SHA256 seeded Faker values:

```
masked_value = Faker(category).generate(seed=HMAC-SHA256(masking_salt || real_value))
```

- Same real value always produces the same masked value (referential integrity preserved).
- Not reversible without the `MASKING_SALT` secret.
- Masked output contains no real PII.

### 2.3 Differential Privacy Synthesis (Synthesize Mode)

Conclave trains a CTGAN model using Opacus DP-SGD (discriminator-level, per ADR-0036).

Formal guarantees:

- **(epsilon, delta)-DP**: Satisfies (ε, δ = 1e-5)-DP. Any single source record's influence is bounded by ε.
- **No memorization**: The Generator trains on Discriminator outputs, not directly on real data. Discriminator gradient updates are privatized by DP-SGD before influencing the Generator.
- **Epsilon budget enforcement**: `spend_budget` in `modules/privacy/accountant.py` raises `BudgetExhaustionError` if the job would exceed the configured budget. Spending is atomic (`SELECT ... FOR UPDATE` locking prevents concurrent overruns).

Synthesized output is not PII under GDPR Recital 26 or CCPA § 1798.140(v): it is generated data with formal DP guarantees, cannot be re-identified with reasonable effort, and shares no exact values with the source.

### 2.4 What Leaves the Air Gap

Nothing. Source data, intermediate artifacts, and synthetic output remain within the operator's deployment perimeter. Conclave makes no external network calls — no telemetry, license call-home, or model registry upload.

---

## 3. Right-to-Erasure Procedure (GDPR Article 17 / CCPA § 1798.105)

### 3.1 What the Erasure Endpoint Deletes

`DELETE /compliance/erasure` accepts a data subject identifier and cascades:

| Record type | Action | Justification |
|-------------|--------|---------------|
| Source connection metadata referencing the subject | Deleted | Direct PII reference |
| Job records that processed the subject's data | Deleted | Indirect association |
| Synthesized output in target DB | **Preserved** | DP-protected; not attributable to any individual |
| Audit trail entries | **Preserved** | Required for compliance proof |
| WORM hash chain | **Preserved** | Deletion would break cryptographic integrity |

The endpoint returns a compliance receipt documenting every deletion and every preservation with written justification. Implementation: `bootstrapper/routers/compliance.py` and `modules/synthesizer/erasure.py`. See ADR-0041.

### 3.2 Preservation Justifications

**Synthesized output** is preserved because DP synthetic data is not personal data under GDPR Recital 26. No individual's contribution is statistically detectable; deletion would provide no privacy benefit while destroying legitimate derivative work.

**Audit trail entries** are preserved under Article 17(3)(b) (legal obligation) and 17(3)(e) (legal claims). The audit trail proves lawful processing and demonstrates erasure compliance.

### 3.3 Vault-Sealed State

If the vault is sealed, the endpoint returns `423 Locked`. ALE-encrypted records cannot be identified without the KEK derived at unseal time. Unseal the vault before processing erasure requests.

### 3.4 Erasure Logging

The erasure request (who, when, count deleted, what preserved) is logged to the WORM audit trail. The subject identifier is never written into the audit payload (CONSTITUTION Priority 0: no PII in audit payloads). The entry is permanent and forms the durable compliance proof.

---

## 4. Audit Trail Immutability Guarantees

The audit log is a WORM (Write-Once, Read-Many) append-only trail.

### 4.1 Cryptographic Integrity

Each event is HMAC-SHA256 signed with `AUDIT_KEY` before append. The signature covers payload, timestamp, and sequence number. Tampering (modification, deletion, or reordering) is detectable by recomputing the HMAC chain.

### 4.2 Append-Only Enforcement

`AuditLogger` exposes only `log_event()`. There are no `delete()`, `update()`, or `truncate()` methods. The module boundary in `shared/` enforces this — the application has no code path to delete an audit event during the retention period.

### 4.3 Retention Floor

Audit events are never deleted within `AUDIT_RETENTION_DAYS` (default 3 years). The automated purge task explicitly excludes the audit table. After the retention period, events may be archived but must not be permanently deleted until the operator confirms no open investigations or legal holds apply.

---

## 5. Retention Configuration

| Setting | Environment variable | Default | Description |
|---------|---------------------|---------|-------------|
| `job_retention_days` | `JOB_RETENTION_DAYS` | `90` | Days before completed/failed jobs are purged |
| `audit_retention_days` | `AUDIT_RETENTION_DAYS` | `1095` | Days audit events are retained before archival eligibility |
| `artifact_retention_days` | `ARTIFACT_RETENTION_DAYS` | `30` | Days Parquet artifacts and model checkpoints are retained |

```bash
# .env — compliance retention overrides
JOB_RETENTION_DAYS=180          # 6 months for financial compliance
AUDIT_RETENTION_DAYS=2555       # 7 years for financial-sector requirements
ARTIFACT_RETENTION_DAYS=14      # 2-week artifact window for sensitive deployments
```

Operator configuration: [OPERATOR_MANUAL.md — Section 11](OPERATOR_MANUAL.md).

### 5.1 Retention Period Guidance by Regulatory Context

| Regulatory context | Recommended `AUDIT_RETENTION_DAYS` | Basis |
|--------------------|------------------------------------|-------|
| GDPR (EU) | 1,095 (3 years) | GDPR Article 5(1)(e); legal claims window |
| CCPA (California) | 1,095 (3 years) | CCPA § 1798.100(e) |
| HIPAA (US healthcare) | 2,190 (6 years) | 45 CFR § 164.530(j) |
| Financial services (SEC Rule 17a-4) | 2,555 (7 years) | SEC Rule 17a-4(b)(1) |

Consult legal counsel for your specific jurisdiction and data category.

---

## 6. Legal Hold Mechanism

A `legal_hold` boolean on `SynthesisJob` records prevents deletion regardless of TTL.

### 6.1 Setting a Legal Hold

```bash
# Enable hold
curl -X PATCH http://<host>:8000/admin/jobs/<job-id>/legal-hold \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"enable": true}'

# Release hold
curl -X PATCH http://<host>:8000/admin/jobs/<job-id>/legal-hold \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"enable": false}'
```

Every hold and release is logged to the WORM audit trail with the requesting operator's identity and timestamp.

### 6.2 Hold Semantics

- Held jobs are skipped by the automated purge task.
- Held jobs cannot be deleted via `DELETE /jobs/<id>`.
- Legal holds survive vault reseal/unseal cycles.
- Releasing a hold restores eligibility for routine purge at the next purge cycle — it does not immediately delete the job.

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

All processing occurs within the operator's deployment perimeter. No data leaves the network boundary at any stage.

---

## 8. Automated Purge Task

Wired to Huey `@periodic_task` cron jobs in `modules/synthesizer/retention_tasks.py`. See ADR-0041.

On each execution:

1. Query `synthesis_job` for records where `created_at < NOW() - job_retention_days` AND `legal_hold = false` AND `status IN ('COMPLETE', 'FAILED', 'SHREDDED')`.
2. For each eligible job, shred artifacts (NIST SP 800-88) and delete the job record.
3. Query MinIO for Parquet artifacts older than `artifact_retention_days` not associated with a legal-held job.
4. Delete eligible artifacts.
5. Log a purge summary to the WORM audit trail (jobs deleted, artifacts deleted, any errors).
6. Never touch audit events.

Configuration: `src/synth_engine/modules/synthesizer/retention.py`.

---

## 9. Compliance Certifications and Attestations

Conclave provides these compliance-relevant properties. Operators are responsible for ensuring their deployment and operational practices satisfy applicable regulations.

| Property | Implementation | Evidence |
|----------|---------------|---------|
| Data minimization (GDPR Art. 5(1)(c)) | Read-only ingestion; source data never persisted to disk in raw form | Pre-flight privilege check; ingestion module design |
| Storage limitation (GDPR Art. 5(1)(e)) | Configurable retention TTLs; automated purge task | `ConclaveSettings` retention fields; `modules/synthesizer/retention.py` |
| Right to erasure (GDPR Art. 17) | `DELETE /compliance/erasure` with cascade deletion and compliance receipt | `modules/synthesizer/erasure.py`; `bootstrapper/routers/compliance.py` |
| Audit trail integrity | WORM, HMAC-SHA256 signed, append-only; no delete path in application code | `shared/security/audit.py` |
| Formal privacy guarantee | (ε, δ)-DP on synthesized output via Opacus DP-SGD | `spend_budget` / `reset_budget` in `modules/privacy/accountant.py`; ADR-0036 |
| Air-gap compliance | No external network calls; offline license activation | Network isolation design; `make build-airgap-bundle` |
| Cryptographic erasure | NIST SP 800-88 compliant shredding of synthesis artifacts | `modules/synthesizer/shred.py`; ADR-0034 |
| Legal hold | `legal_hold` boolean on job records; prevents purge | `PATCH /admin/jobs/{id}/legal-hold`; `bootstrapper/routers/admin.py` |

---

## 10. Operator Responsibilities

1. **Configure retention periods** appropriate for your regulatory context before production (see [Section 5](#5-retention-configuration)).
2. **Schedule the purge task** at an appropriate cadence (daily recommended).
3. **Protect `AUDIT_KEY`** and rotate it per [OPERATOR_MANUAL.md](OPERATOR_MANUAL.md). Loss renders the audit trail unverifiable.
4. **Maintain a data processing record** (GDPR Article 30). Conclave's audit trail provides technical evidence but does not replace it.
5. **Process erasure requests promptly**. GDPR Article 12(3) requires erasure within one calendar month. Use `DELETE /compliance/erasure` programmatically.
6. **Test the erasure endpoint** before production to verify cascade deletion behaves as expected for your schema.
