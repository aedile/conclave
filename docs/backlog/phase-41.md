# Phase 41 — Data Compliance, Retention Policy & GDPR/CCPA Readiness

**Goal**: Implement data retention policies, GDPR Article 17 right-to-erasure,
CCPA deletion mechanisms, and close the compliance gaps identified in the
2026-03-19 Security Audit. Ensure the system can pass a compliance audit.

**Prerequisite**: Phase 40 merged. Zero open advisories.

**ADR**: ADR-0040 — Data Retention & Compliance Architecture (new, required).
Must document: retention periods, deletion cascades, audit trail preservation
requirements, and legal hold mechanisms.

**Source**: Production Readiness Audit, 2026-03-19 — High Issue H4, Compliance findings.

---

## T41.1 — Implement Data Retention Policy

**Priority**: P0 — Compliance. `synthesis_job` table retains all records indefinitely.
Audit events have no documented purge cycle. No TTL on any data.

### Context & Constraints

1. The system stores three categories of data with different retention needs:
   - **Job records** (`synthesis_job` table): Contain job metadata, status,
     epsilon spent, error messages. Should have a configurable TTL.
   - **Audit events** (WORM audit trail): Must be retained for compliance
     audit period (e.g., 7 years for financial, 3 years for GDPR). Must NOT
     be deleted by routine purges.
   - **Artifacts** (MinIO/S3 Parquet files): Ephemeral by design (container
     restart clears them), but completed artifacts may persist if container
     runs long-term.

2. Retention configuration must be in `ConclaveSettings`:
   - `job_retention_days: int = 90` (default 90 days)
   - `audit_retention_days: int = 1095` (default 3 years)
   - `artifact_retention_days: int = 30` (default 30 days)

3. A periodic cleanup task (Huey scheduled task or CLI command) must:
   - Delete `synthesis_job` records older than `job_retention_days`
   - Delete Parquet artifacts older than `artifact_retention_days`
   - NEVER delete audit events within `audit_retention_days`
   - Log all deletions to the audit trail (meta-audit)

4. Audit events must be append-only during the retention period. After the
   retention period, they may be archived (not deleted) to cold storage.

5. Legal hold mechanism: a `legal_hold` boolean on job records that prevents
   deletion regardless of TTL. Must be settable via API endpoint.

### Acceptance Criteria

1. `ConclaveSettings` has retention period fields with sensible defaults.
2. Cleanup task deletes expired jobs and artifacts.
3. Audit events are never deleted within retention period.
4. Legal hold prevents deletion of held records.
5. All deletions logged to audit trail.
6. ADR-0040 documents retention architecture.
7. New tests: expired job deleted, non-expired job retained, legal-held job
   retained despite expiry, audit events preserved.
8. Full gate suite passes.

### Files to Create/Modify

- Modify: `src/synth_engine/shared/settings.py` (retention settings)
- Create: `src/synth_engine/modules/synthesizer/retention.py` (cleanup logic)
- Modify: `src/synth_engine/modules/synthesizer/job_models.py` (legal_hold field)
- Create: `src/synth_engine/bootstrapper/routers/admin.py` (legal hold endpoint)
- Create: `docs/adr/ADR-0040-data-retention-compliance.md`
- Create: `tests/unit/test_retention.py`
- Create: `tests/integration/test_retention_cleanup.py`

---

## T41.2 — Implement GDPR Right-to-Erasure & CCPA Deletion Endpoint

**Priority**: P0 — Compliance. No mechanism exists for data subjects to request
deletion of their data, as required by GDPR Article 17 and CCPA.

### Context & Constraints

1. The system processes source data (PII) through masking and synthesis. The
   synthesized output is differentially private and does not constitute PII.
   However, intermediate artifacts, job metadata, and audit records may
   reference source data identifiers.

2. A `DELETE /compliance/erasure` endpoint must:
   - Accept a data subject identifier (e.g., source record ID, email hash)
   - Cascade deletion through: connection metadata, job records that
     processed the subject's data, intermediate artifacts
   - NOT delete: synthesized output (it's DP-protected and non-attributable),
     audit trail entries (required for compliance proof), WORM hash chain
   - Return a compliance receipt (RFC 7807 success format) documenting
     what was deleted and what was retained (with justification)

3. The audit trail must record the erasure request itself (who requested,
   when, what was deleted) — this is the compliance proof.

4. If the vault is sealed, erasure cannot proceed (ALE-encrypted data
   cannot be identified for deletion). Return 423.

5. Rate limit: erasure requests limited to 1/minute per operator (prevent
   bulk deletion attacks).

### Acceptance Criteria

1. `DELETE /compliance/erasure` endpoint accepts subject identifier.
2. Cascade deletion removes connection metadata and job records referencing
   the subject.
3. Synthesized output and audit trail preserved (with documentation).
4. Compliance receipt returned with deletion manifest.
5. Erasure request logged to audit trail.
6. Vault-sealed state returns 423.
7. New tests: erasure deletes correct records, preserves audit trail,
   returns compliance receipt, fails when vault sealed.
8. Full gate suite passes.

### Files to Create/Modify

- Create: `src/synth_engine/bootstrapper/routers/compliance.py`
- Create: `src/synth_engine/modules/synthesizer/erasure.py`
- Modify: `src/synth_engine/bootstrapper/router_registry.py` (register route)
- Create: `tests/unit/test_erasure.py`
- Create: `tests/integration/test_compliance_erasure.py`

---

## T41.3 — Document Data Retention & Compliance Policies

**Priority**: P1 — Documentation. No user-facing documentation of retention
policies, data handling practices, or compliance mechanisms.

### Context & Constraints

1. Create `docs/DATA_COMPLIANCE.md` covering:
   - Data categories and their retention periods
   - What PII is processed and how (masking, synthesis, DP guarantees)
   - Right-to-erasure procedure and what it deletes/preserves
   - Audit trail immutability guarantees
   - Legal hold mechanism
   - Data flow diagram: source → masking → synthesis → output

2. Update `README.md` compliance section to reference the new document.

3. Update `OPERATOR_MANUAL.md` with retention configuration instructions.

### Acceptance Criteria

1. `docs/DATA_COMPLIANCE.md` covers all compliance topics.
2. README references the compliance document.
3. Operator manual includes retention configuration.
4. Markdownlint passes.

### Files to Create/Modify

- Create: `docs/DATA_COMPLIANCE.md`
- Modify: `README.md`
- Modify: `docs/OPERATOR_MANUAL.md`

---

## Task Execution Order

```
T41.1 (Retention policy) ──────────> sequential (T41.2 depends on retention infra)
T41.2 (Erasure endpoint) ──────────> after T41.1
T41.3 (Compliance docs) ───────────> parallel with T41.1/T41.2
```

---

## Phase 41 Exit Criteria

1. Data retention policy implemented with configurable TTLs.
2. GDPR/CCPA erasure endpoint functional with cascade deletion.
3. Legal hold mechanism prevents premature deletion.
4. Compliance documentation complete.
5. ADR-0040 documents the architecture.
6. All quality gates pass.
7. Zero open advisories in RETRO_LOG.
8. Review agents pass for all tasks.
