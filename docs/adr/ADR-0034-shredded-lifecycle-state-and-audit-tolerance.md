# ADR-0034: SHREDDED Lifecycle State and Audit Tolerance

**Status:** Accepted
**Date:** 2026-03-17
**Deciders:** PM, Architecture Reviewer, QA Reviewer
**Task:** P23-T23.4-cryptographic-erasure

---

## Context

NIST SP 800-88 (Rev. 1) requires that media containing sensitive data be sanitised
before release or disposal.  In the Synthetic Data Generation Engine, synthesis jobs
produce three artifact files:

- The generated synthetic Parquet file (`output_path`).
- The HMAC-SHA256 signature sidecar (`output_path + ".sig"`).
- The trained model artifact pickle (`artifact_path`).

These artifacts must be irreversibly deleted on operator request.  A `POST
/jobs/{id}/shred` endpoint was introduced in P23-T23.4 to perform this deletion.

Two design questions arose:

1. **What lifecycle state should a job occupy after erasure?**  The job record must
   remain in the database (for audit lineage) but must be marked in a way that
   prevents re-download or re-shredding.
2. **What happens if the WORM audit log call fails after the files are already
   deleted?**  Aborting and returning an error would leave the job in `COMPLETE`
   status with no files on disk — a ghost-state that is more dangerous than a
   missing audit entry.

---

## Decision

### 1. SHREDDED lifecycle state

A new terminal status value, `SHREDDED`, is introduced for `SynthesisJob`.

- **Transition**: `COMPLETE` → `SHREDDED`.  This is a one-way, irreversible
  transition.  No other status may transition to `SHREDDED`.
- **Guard**: Only jobs with `status == COMPLETE` are eligible for shredding.
  Attempting to shred a job in any other status (including `SHREDDED`) returns
  HTTP 404 Problem Detail.
- **Persistence**: The job record is retained in the database after shredding.
  The `output_path` and `artifact_path` fields retain their last-known values
  as a forensic record of what was deleted.

### 2. Erasure scope

The following files are deleted by `shred_artifacts()` (NIST 800-88 "Clear"):

| File | Field | Notes |
|------|-------|-------|
| Synthetic Parquet | `output_path` | Primary artifact |
| HMAC sidecar | `output_path + ".sig"` | Integrity proof deleted alongside artifact |
| Model pickle | `artifact_path` | Trained weights; may allow re-synthesis |

Deletion is performed via `pathlib.Path.unlink(missing_ok=True)`.  Files that are
already absent are silently skipped so the operation is idempotent.

### 3. Audit tolerance

The WORM audit `log_event("ARTIFACT_SHREDDED", ...)` call is made **after**
successful file deletion.  If it raises any exception:

- The exception is swallowed (after logging at `ERROR` level).
- The job status transition to `SHREDDED` proceeds unconditionally.

**Rationale**: Aborting after deletion would leave the job in `COMPLETE` with no
artifact files — a ghost state that is harder to reason about and could allow
infinite retry loops.  An audit entry gap is recoverable from server logs; a
ghost state requires manual intervention.

### 4. OSError handling in the router

If `shred_artifacts()` raises `OSError` (e.g., permission denied):

- The router logs at `ERROR` level using the exception class name only (never the
  full filesystem path) to prevent internal topology leakage.
- The router returns HTTP 500 with RFC 7807 Problem Detail body.
- The job status remains `COMPLETE` (no status transition occurs).

### 5. NIST 800-88 method: Clear

`Path.unlink()` issues a single `unlink(2)` syscall.  On tmpfs-backed MinIO
ephemeral storage (the production environment), unlinked blocks are immediately
reclaimed by the kernel and inaccessible without physical media forensics.  This
satisfies NIST 800-88 "Clear" for volatile / ephemeral storage.

**Must be revisited for persistent block storage**: If the system is deployed on
persistent HDDs or SSDs without full-disk encryption, "Clear" alone may not prevent
data recovery.  In that environment, "Purge" (cryptographic erase or overwrite) is
required.  A follow-up task must be created before any such deployment.

---

## Consequences

**Positive:**
- Jobs in `SHREDDED` state are clearly marked; no accidental re-download or
  re-shredding is possible.
- Audit log failures cannot corrupt the lifecycle state machine.
- OSError from the filesystem returns a structured RFC 7807 response rather than
  an unhandled 500 with no body.
- Idempotent erasure matches NIST 800-88's allowance for repeated safe operations.

**Negative / Constraints:**
- A missing audit entry after shredding is not immediately visible to operators;
  they must inspect server logs to detect audit failures.
- The "Clear" method is only sufficient for ephemeral/tmpfs storage.  Persistent
  block storage deployments require a revisit (see above).

---

## Alternatives Considered

**Abort on audit failure**: Rejected because it creates ghost state (COMPLETE with
no artifacts) that is harder to recover from than a missing audit entry.

**Delete the job record after shredding**: Rejected because it destroys audit
lineage and prevents operators from knowing that a job existed.

**Status `DELETED` instead of `SHREDDED`**: Rejected because `SHREDDED` is more
specific to the NIST 800-88 context and immediately communicates the security
operation that was performed.

---

## References

- NIST SP 800-88 Rev. 1 — Guidelines for Media Sanitization
- ADR-0010 — WORM audit logger design
- `src/synth_engine/modules/synthesizer/shred.py` — domain erasure function
- `src/synth_engine/bootstrapper/routers/jobs.py` — shred endpoint router
- `tests/unit/test_shred_endpoint.py` — acceptance tests
