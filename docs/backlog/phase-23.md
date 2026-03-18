# Phase 23 — Job Lifecycle Completion

**Historical summary.** This file is a backfill record, not a planning document.
Phase 23 was executed on 2026-03-17 and merged across five PRs.

---

## PRs Merged

| PR | Title | Merged |
|----|-------|--------|
| [#113](../../pull/113) | feat(P23-T23.1): generation step in Huey synthesis task | 2026-03-17 |
| [#114](../../pull/114) | feat(P23-T23.2): /jobs/{id}/download streaming endpoint | 2026-03-17 |
| [#115](../../pull/115) | feat(P23-T23.4): cryptographic erasure endpoint (NIST 800-88) | 2026-03-17 |
| [#116](../../pull/116) | docs: README marketing pass — capabilities-first rewrite | 2026-03-17 |
| [#117](../../pull/117) | feat(P23-T23.3): frontend Download button for COMPLETE jobs | 2026-03-17 |

---

## Key Deliverables

- **Generation step (T23.1)**: Completed the Huey synthesis task with a generation step that
  produces real Parquet artifacts signed with HMAC. Fixed: `_write_parquet_with_signing` had
  no exception handler — a signing failure would leave the job permanently stuck in
  `GENERATING` state. Fixed with try/except and FAILED transition.

- **Download endpoint (T23.2)**: `GET /jobs/{id}/download` streams the Parquet artifact.
  Verifies the HMAC signature before serving. Fixed Content-Disposition header injection:
  `table_name` was passed unsanitized to the HTTP header. A user controlling the table name
  could inject arbitrary header content. Fixed with `^[a-zA-Z0-9_]+$` regex validation and
  defense-in-depth `_sanitize_filename()`.

- **Cryptographic erasure (T23.4)**: `POST /jobs/{id}/shred` implements NIST SP 800-88
  cryptographic erasure — deletes the Parquet artifact and MinIO object, then sets job
  status to `SHREDDED`. Non-reversible.

- **Frontend download (T23.3)**: Download button appears for COMPLETE jobs. Fixed path
  traversal in `extractFilename` — server-supplied filename was passed directly to
  `anchor.download` without sanitization. Fixed with `sanitizeFilename()` stripping `/`
  and `\`.

- **README marketing pass**: Rewritten to lead with capabilities and threat model rather
  than implementation details.

---

## Retrospective Notes

- Security finding pattern: any user-supplied value (table name, filename) must be sanitized
  before appearing in HTTP headers or `anchor.download` attributes.
- Missing exception handler on artifact writes → job stuck in intermediate state. All
  artifact write paths must have try/except with explicit FAILED transition.
- `str(exc)` in API error messages: identified as recurring PII risk; scheduled for
  project-wide fix in Phase 26.
