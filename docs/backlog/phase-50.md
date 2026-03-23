# Phase 50 — Production Security Fixes

**Goal**: Fix five critical production defects identified in the architecture
review that will cause failures, compliance violations, or security bypasses in
production deployments.

**Prerequisite**: Phase 48 merged (infrastructure fixes). Phase 49 preferred but
not blocking — these are production code fixes with their own test requirements.

**ADR**: T50.1 and T50.2 require new ADRs. T50.5 may require an ADR if
serialization format changes.

**Source**: Staff-level architecture review, 2026-03-22 — penetration test
findings and production readiness audit.

---

## T50.1 — DP Budget Deduction: Fail Closed

**Priority**: P0 — Security/Compliance. Silent budget failure means privacy
guarantee is violated.

### Context & Constraints

1. `dp_accounting.py:140` catches `Exception` broadly when calling
   `spend_budget_fn`. If the budget deduction fails for any reason, the job
   continues. The epsilon ledger is now wrong.
2. The system's value proposition is (epsilon, delta)-DP guarantees. A failed budget
   deduction means the reported epsilon is lower than the actual privacy cost. This
   is a compliance-critical defect.
3. `BudgetExhaustionError` and `EpsilonMeasurementError` already exist in the
   exception hierarchy.
4. The fix is narrow: catch only the expected exceptions, let everything else
   propagate and abort the job.

### Acceptance Criteria

1. `dp_accounting.py` broad `except Exception` replaced with specific catches:
   `BudgetExhaustionError`, `EpsilonMeasurementError`.
2. Any other exception from `spend_budget_fn` propagates, marking the job FAILED.
3. New test: inject an unexpected exception into `spend_budget_fn` -> verify job
   status is FAILED (not COMPLETE).
4. New test: `BudgetExhaustionError` from `spend_budget_fn` -> verify budget
   exhaustion is handled gracefully (existing behavior preserved).
5. ADR documenting the fail-closed decision and compliance rationale.
6. Full gate suite passes.

### Files to Create/Modify

- Modify: `src/synth_engine/modules/synthesizer/dp_accounting.py`
- Create: `docs/adr/ADR-0048-dp-budget-fail-closed.md`
- Modify: `tests/unit/test_dp_accounting.py`

---

## T50.2 — Multi-Pod Audit Chain Integrity

**Priority**: P0 — Security. WORM audit trail tamper-evidence broken in multi-pod
deployment.

### Context & Constraints

1. `shared/security/audit.py` uses `threading.Lock` for hash chain integrity.
   This is process-scoped.
2. In Kubernetes (2+ pods), each pod independently computes chain hashes. Two pods
   writing audit entries produce independent chains with potentially overlapping
   sequence numbers.
3. The tamper-evident property (hash chain) is the core security guarantee of WORM
   logging.
4. Options: (a) Database-backed sequence (PostgreSQL SERIAL + advisory lock),
   (b) Singleton audit writer sidecar, (c) Per-pod chain prefix with centralized
   verification.
5. Option (a) is simplest and aligns with existing PostgreSQL infrastructure.

### Acceptance Criteria

1. Audit chain sequence numbers are globally unique across pods (not just
   per-process).
2. Hash chain integrity is verifiable across entries from multiple pods.
3. Audit write performance does not degrade by more than 2x (database round-trip
   acceptable, but not blocking).
4. New integration test: simulate 2 concurrent "pods" (2 threads with separate
   AuditLogger instances) writing interleaved entries. Verify combined chain is
   valid.
5. ADR documenting chosen approach and trade-offs.
6. Full gate suite passes.

### Files to Create/Modify

- Modify: `src/synth_engine/shared/security/audit.py`
- Create: `docs/adr/ADR-0049-multi-pod-audit-chain.md`
- Modify: `tests/unit/test_audit_logger.py`
- Create: `tests/integration/test_audit_chain_concurrency.py`

---

## T50.3 — Default to Production Mode

**Priority**: P0 — Security. Missing env var silently disables authentication.

### Context & Constraints

1. Empty `JWT_SECRET_KEY` disables authentication entirely (dev convenience).
2. Production mode check depends on `CONCLAVE_ENV=production`. If this env var is
   missing, the system defaults to dev mode.
3. A fresh deployment with no `.env` boots with no auth — the exact opposite of
   secure-by-default.
4. Fix: default `CONCLAVE_ENV` to `production`. Dev mode must be explicitly opted
   into.

### Acceptance Criteria

1. `CONCLAVE_ENV` defaults to `"production"` when not set (not `"development"`).
2. Dev mode requires explicit `CONCLAVE_ENV=development`.
3. Startup logs WARNING when running in dev mode: "Authentication disabled —
   development mode active. Set CONCLAVE_ENV=production for production use."
4. Missing `JWT_SECRET_KEY` in production mode -> startup fails with clear error
   (existing behavior, just verify).
5. New test: no `CONCLAVE_ENV` set -> production mode enforced, auth required.
6. New test: `CONCLAVE_ENV=development` -> dev mode, auth disabled with warning.
7. Full gate suite passes.

### Files to Create/Modify

- Modify: `src/synth_engine/shared/settings.py` (default CONCLAVE_ENV)
- Modify: `src/synth_engine/bootstrapper/config_validation.py`
- Modify: `tests/unit/test_startup_validate_config.py`
- Modify: `tests/unit/test_missing_env_var_startup_check.py`

---

## T50.4 — Pickle TOCTOU Mitigation

**Priority**: P1 — Security. Time-of-check-to-time-of-use gap in artifact
verification.

### Context & Constraints

1. `modules/synthesizer/models.py`: Model artifacts are HMAC-signed at creation
   and verified at load. The verification and deserialization are separate
   operations.
2. If ephemeral storage (MinIO) is compromised between sign-time and load-time, a
   malicious pickle can be substituted.
3. MinIO is on tmpfs (good), but any pod with network access to
   `minio-ephemeral:9000` can write to it.
4. Fix: Verify HMAC immediately before `pickle.loads()` in the same atomic
   function call — no window between verify and load.
5. Longer-term: evaluate migration to safetensors or ONNX (may warrant separate
   ADR).

### Acceptance Criteria

1. HMAC verification and `pickle.loads()` occur in the same function, with no
   external calls between them.
2. The bytes verified by HMAC are the exact same bytes passed to `pickle.loads()`
   (read once, verify, then deserialize from the same buffer).
3. New test: tamper with artifact bytes after signing -> verify load rejects with
   `ArtifactTamperingError`.
4. Close ADV-P47-07.
5. Full gate suite passes.

### Files to Create/Modify

- Modify: `src/synth_engine/modules/synthesizer/models.py`
- Modify: `tests/unit/test_artfact_signing.py` (note: existing typo in filename)

---

## T50.5 — Centralize Remaining Hardcodes

**Priority**: P2 — Maintainability. Hardcoded values require code changes for
environment-specific configuration.

### Context & Constraints

1. Phase 36 centralized 14 env vars into `ConclaveSettings`. These were missed:
   - `_MINIO_ENDPOINT = "http://minio-ephemeral:9000"` (main.py:86)
   - `_EPHEMERAL_BUCKET = "synth-ephemeral"` (main.py:92)
   - `_SECRETS_DIR = Path("/run/secrets")` (main.py:92)
   - Service hostname allowlist in tls/config.py:74-90 (10 hardcoded hostnames)
   - OOM calculation params in job_orchestration.py:91-94 (overhead factor, min
     samples, chunk size, max retries)
2. The pattern exists — just extend `ConclaveSettings` with new fields and replace
   hardcodes with settings reads.
3. Defaults should match current hardcoded values (no behavior change without
   explicit configuration).

### Acceptance Criteria

1. All 5 hardcoded value groups moved to `ConclaveSettings` with sensible defaults
   matching current values.
2. Each new setting is readable from environment variables.
3. `config_validation.py` validates new settings where appropriate (e.g.,
   MINIO_ENDPOINT must be a valid URL).
4. Tests: verify each setting is read from env, defaults match previous hardcoded
   values.
5. Full gate suite passes.

### Files to Create/Modify

- Modify: `src/synth_engine/shared/settings.py`
- Modify: `src/synth_engine/bootstrapper/main.py`
- Modify: `src/synth_engine/shared/tls/config.py`
- Modify: `src/synth_engine/modules/synthesizer/job_orchestration.py`
- Modify: `src/synth_engine/bootstrapper/config_validation.py`
- Modify: `tests/unit/test_startup_validate_config.py`

---

## Task Execution Order

```
T50.1 (DP budget fail-closed) ─────────┐
T50.2 (Multi-pod audit chain) ─────────┼──> parallel (P0 security fixes)
T50.3 (Default to production mode) ────┘
                                          ↓ P0 fixes complete
T50.4 (Pickle TOCTOU mitigation) ──────┐
T50.5 (Centralize remaining hardcodes) ┼──> parallel (P1/P2 hardening)
                                        ┘
```

T50.1, T50.2, and T50.3 are independent P0 security fixes that can run in
parallel. T50.4 and T50.5 are lower-priority hardening tasks that can also run in
parallel with each other but should follow the P0 fixes.

---

## Phase 50 Exit Criteria

1. DP budget deduction fails closed on unexpected exceptions.
2. Audit chain integrity maintained across multiple pods.
3. Missing `CONCLAVE_ENV` defaults to production mode (secure-by-default).
4. Pickle deserialization is atomic with HMAC verification (no TOCTOU window).
5. All remaining hardcoded values centralized in `ConclaveSettings`.
6. ADRs written for fail-closed budget (ADR-0048) and multi-pod audit (ADR-0049).
7. All quality gates pass.
8. Zero open advisories in RETRO_LOG.
9. Review agents pass for all tasks.
