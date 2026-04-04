# Phase 85 — Audit Export & Compliance Reporting

**Tier**: 8 (Enterprise Scale)
**Goal**: Provide exportable audit trails and per-job compliance reports for external
auditors and regulatory review.

**Dependencies**: Phase 79 (multi-tenancy — reports scoped to org), Phase 80 (RBAC — auditor role)

---

## Prerequisites

### T85.0 — PDF Generation Library ADR (Rule 6)

Select the PDF generation library before implementation begins. Candidates: `reportlab`
(pure Python, large), `fpdf2` (pure Python, lightweight), `weasyprint` (C deps for
Cairo/Pango — air-gap bundle concern). ADR must document:
- Library selection with air-gap bundle impact
- Native binary dependencies (if any)
- `pip-audit` results

### T85.0b — Persistent Artifact Storage

The current MinIO service in docker-compose uses tmpfs (ephemeral). Scheduled audit exports
to MinIO (T85.4) require persistent storage. Either:
- Add a persistent MinIO service/volume to docker-compose, or
- Change the export target to a filesystem path (simpler for air-gap deployments)

This must be resolved before T85.4 implementation.

---

## Context & Constraints

- The WORM audit log exists (`shared/security/audit.py`) with HMAC signature chains.
- There's no way to export it. An auditor must currently read the database directly.
- Compliance officers need per-job reports: what data was processed, what privacy
  guarantee was applied, what masking was used, chain of custody from source to output.
- Reports must be cryptographically signed so their integrity can be verified offline.
- Export must handle large audit logs without OOM (streaming/pagination).
- **Compliance report data flow**: The per-job compliance report and chain-of-custody
  report span data from multiple modules (ingestion, subsetting, masking, synthesis,
  privacy). These reports MUST NOT live in `modules/synthesizer/` — they are cross-cutting
  compliance concerns. Place in `shared/compliance/` as a new subpackage. The bootstrapper
  assembles the report data from multiple modules and passes it to the report generator.
  No cross-module imports from within `shared/compliance/`.
- **Streaming queries**: `StreamingResponse` with SQLAlchemy async `stream_scalars()` or
  `yield_per()` is a new pattern in this codebase. Session lifecycle across chunk
  boundaries must be managed carefully to avoid connection pool exhaustion.

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
- `src/synth_engine/shared/compliance/__init__.py` (new subpackage)
- `src/synth_engine/shared/compliance/report_generator.py` (new)
- `bootstrapper/routers/compliance.py` (add report endpoint)

**Acceptance Criteria**:
- [ ] `GET /api/v1/jobs/{job_id}/compliance-report` — generate compliance report
- [ ] Report contents: job metadata, source tables processed, row counts,
      masking algorithms applied per column, epsilon/delta values per table,
      noise multiplier settings, model training parameters, artifact signatures
- [ ] Report format: JSON (machine-readable) and PDF (human-readable, per T85.0 ADR)
- [ ] Report is signed with the artifact signing key for integrity verification
- [ ] Report can be verified offline (includes public key fingerprint and verification instructions)
- [ ] Report data assembled by the bootstrapper from multiple module outputs — no
      cross-module imports within `shared/compliance/`

### T85.3 — Chain-of-Custody Report

**Files to create**:
- `src/synth_engine/shared/compliance/chain_of_custody.py` (new — in `shared/compliance/`,
  NOT in `modules/synthesizer/`. This report spans ingestion, subsetting, masking,
  synthesis, and privacy data.)

**Acceptance Criteria**:
- [ ] Report traces data lineage: source connection → ingestion → subsetting → masking/synthesis → output
- [ ] Each step includes: timestamp, operator identity, input hash, output hash, parameters
- [ ] Cryptographically signed end-to-end (any tampering breaks the chain)
- [ ] Includes privacy budget before and after the job
- [ ] Machine-readable JSON format for automated compliance tooling
- [ ] Data passed in by the bootstrapper as a structured DTO — the chain-of-custody
      generator does not import from any module

### T85.4 — Scheduled Audit Export

**Files to create/modify**:
- `src/synth_engine/shared/tasks/audit_export.py` (new Huey task)
- `shared/settings.py` (export schedule config)

**Acceptance Criteria**:
- [ ] Configurable schedule: `AUDIT_EXPORT_SCHEDULE` (daily, weekly, monthly)
- [ ] Export target: configured filesystem path or S3/MinIO bucket path (per T85.0b)
- [ ] Scheduled export covers all periods since last successful export (no gaps on
      consecutive failures)
- [ ] Export file named with date range and org_id for easy retrieval
- [ ] Failure to export → alert via webhook + retry on next schedule
- [ ] Auto-export failure increments `conclave_audit_export_failures_total` Prometheus counter
- [ ] `.env.example` updated with `AUDIT_EXPORT_SCHEDULE` and export target config
- [ ] Add runbook: `docs/runbooks/audit-export-failure.md`

---

## Testing & Quality Gates

- Attack tests: viewer attempts audit export (403), operator attempts audit export (403)
- Attack tests: Org A attempts to export Org B's audit log (404)
- Integration tests: create audit events → export → verify all events present and chain intact
- Performance test: export 100K audit entries without OOM (streaming verified)
- Signature verification test: tampered export detected
- PDF generation test: report renders without errors for a complete job lifecycle
- Backward compatibility: existing compliance endpoint tests pass unchanged
