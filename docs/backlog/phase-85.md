# Phase 85 — Audit Export & Compliance Reporting

**Tier**: 8 (Enterprise Scale)
**Goal**: Provide exportable audit trails and per-job compliance reports for external
auditors and regulatory review.

**Dependencies**: Phase 79 (multi-tenancy — reports scoped to org), Phase 80 (RBAC — auditor role)

---

## Context & Constraints

- The WORM audit log exists (`shared/security/audit.py`) with HMAC signature chains.
- There's no way to export it. An auditor must currently read the database directly.
- Compliance officers need per-job reports: what data was processed, what privacy
  guarantee was applied, what masking was used, chain of custody from source to output.
- Reports must be cryptographically signed so their integrity can be verified offline.
- Export must handle large audit logs without OOM (streaming/pagination).

---

## Tasks

### T85.1 — Audit Export Endpoint

**Files to create**:
- `bootstrapper/routers/audit_export.py` (new)
- `bootstrapper/schemas/audit_export.py` (new)

**Acceptance Criteria**:
- [ ] `GET /api/v1/compliance/audit-export` — export audit log entries
- [ ] Query parameters: `start_date`, `end_date`, `event_type`, `format` (csv, json, jsonl)
- [ ] Streaming response for large exports (no full materialization in memory)
- [ ] Scoped to requesting org (multi-tenant isolation)
- [ ] Accessible by `auditor` and `admin` roles only
- [ ] Each export is itself an audit event (audit the export)
- [ ] HMAC signature chain verified during export — broken chain flagged in output

### T85.2 — Per-Job Compliance Report

**Files to create**:
- `src/synth_engine/modules/synthesizer/compliance_report.py` (new)
- `bootstrapper/routers/compliance.py` (add report endpoint)

**Acceptance Criteria**:
- [ ] `GET /api/v1/jobs/{job_id}/compliance-report` — generate compliance report
- [ ] Report contents: job metadata, source tables processed, row counts,
      masking algorithms applied per column, epsilon/delta values per table,
      noise multiplier settings, model training parameters, artifact signatures
- [ ] Report format: JSON (machine-readable) and PDF (human-readable)
- [ ] Report is signed with the artifact signing key for integrity verification
- [ ] Report can be verified offline (includes public key fingerprint and verification instructions)

### T85.3 — Chain-of-Custody Report

**Files to create**:
- `src/synth_engine/modules/synthesizer/chain_of_custody.py` (new)

**Acceptance Criteria**:
- [ ] Report traces data lineage: source connection → ingestion → subsetting → masking/synthesis → output
- [ ] Each step includes: timestamp, operator identity, input hash, output hash, parameters
- [ ] Cryptographically signed end-to-end (any tampering breaks the chain)
- [ ] Includes privacy budget before and after the job
- [ ] Machine-readable JSON format for automated compliance tooling

### T85.4 — Scheduled Audit Export

**Files to create/modify**:
- `src/synth_engine/shared/tasks/audit_export.py` (new Huey task)
- `shared/settings.py` (export schedule config)

**Acceptance Criteria**:
- [ ] Configurable schedule: `AUDIT_EXPORT_SCHEDULE` (daily, weekly, monthly)
- [ ] Export target: configured S3/MinIO bucket path
- [ ] Each scheduled export covers the period since last export
- [ ] Export file named with date range and org_id for easy retrieval
- [ ] Failure to export → alert via webhook + retry on next schedule

---

## Testing & Quality Gates

- Attack tests: viewer attempts audit export (403), operator attempts audit export (403)
- Attack tests: Org A attempts to export Org B's audit log (404)
- Integration tests: create audit events → export → verify all events present and chain intact
- Performance test: export 100K audit entries without OOM (streaming verified)
- Signature verification test: tampered export detected
