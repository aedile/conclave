# Phase 38 — Audit Integrity, Timing Side-Channel Fix & Pre-Commit Hardening

**Goal**: Fix a silent audit failure that allows jobs to complete without WORM trail
entries, close a vault unsealing timing side-channel, enforce import boundary contracts
at commit time, and batch documentation/hygiene polish items.

**Prerequisite**: Phase 37 merged. Zero open advisories.

**ADR**: None required — no architectural decisions, only fixes and enforcement.

**Source**: Panel Roast #4 (post-Phase 37).

---

## T38.1 — Fix Silent Audit Failure After Budget Deduction (CRITICAL)

**Priority**: P0 — Correctness / Security. A DP training run can complete with zero
WORM audit trail entry if the audit logger fails after epsilon is deducted.

### Context & Constraints

1. `job_orchestration.py:259` has an `except Exception` handler that logs ERROR when
   the audit logger fails after a successful budget deduction, but does NOT re-raise.
   The job silently completes with the budget spent but no audit record.

2. This violates Constitution Priority 0 (Security): every privacy budget spend MUST
   have an immutable WORM audit entry. If the audit infrastructure is broken, the job
   output should not be delivered.

3. The T37.1 fix addressed the *epsilon measurement* failure path but did not touch
   the *audit logging* failure path — they are separate exception handlers.

4. Fix: If the audit log write fails after budget deduction, the job must be marked
   FAILED. The budget has already been spent (irreversible), so the FAILED status
   alerts the operator that a manual audit reconciliation is needed.

5. The error message should clearly state: "Budget deducted but audit trail write
   failed — manual reconciliation required."

### Acceptance Criteria

1. If the WORM audit logger raises after budget deduction, the job is marked FAILED.
2. The error message includes "audit trail write failed" and "manual reconciliation".
3. A WARNING-level log entry records the audit infrastructure failure.
4. New test: verify job status is FAILED when audit logger raises after budget spend.
5. Full gate suite passes.

### Files to Create/Modify

- Modify: `src/synth_engine/modules/synthesizer/job_orchestration.py`
- Modify: `tests/unit/test_job_steps.py` (add audit failure test)

---

## T38.2 — Fix Vault Unseal Timing Side-Channel

**Priority**: P1 — Security. Empty passphrase check creates a fast-failure path
distinguishable from wrong-passphrase PBKDF2 timing (~100ms difference).

### Context & Constraints

1. `shared/security/vault.py:116-141` — `VaultState.unseal()` checks
   `if not passphrase: raise VaultEmptyPassphraseError(...)` before running PBKDF2.
   An empty passphrase returns in microseconds; a wrong passphrase takes ~100ms
   (600,000 PBKDF2 iterations).

2. A network-based attacker timing HTTP roundtrips to `/unseal` can distinguish
   "empty vs wrong" passphrases. While this is low-risk (operator passphrases are
   typically strong, production uses TLS + rate-limiting), it's a correctness issue
   for a security-focused system.

3. Fix: Always run PBKDF2 derivation before checking passphrase validity. The empty
   check should happen AFTER the constant-time derivation completes.

4. Alternative: Add a deliberate constant-time delay (e.g., `time.sleep(0.1)`) for
   the empty-passphrase path. This is simpler but less robust than always deriving.

5. Option 3 (recommended): Run PBKDF2 with a dummy salt for empty passphrases, then
   raise the error. This ensures the timing is identical to the real derivation path.

### Acceptance Criteria

1. Empty passphrase and wrong passphrase take approximately the same time (within 10%).
2. `VaultEmptyPassphraseError` is still raised for empty passphrases.
3. New test: verify timing difference between empty and wrong passphrase is <20ms.
4. Full gate suite passes.

### Files to Create/Modify

- Modify: `src/synth_engine/shared/security/vault.py`
- Modify: `tests/unit/test_vault.py` (add timing test)

---

## T38.3 — Add Import-Linter to Pre-Commit Hooks

**Priority**: P1 — Functionality. Import boundary contracts are defined in
`pyproject.toml` but only enforced in CI, not at commit time. Developers can push
boundary-violating code that only fails after the CI build.

### Context & Constraints

1. `pyproject.toml` defines 4 import-linter contracts enforcing module boundaries.
   These are verified in CI but not in `.pre-commit-config.yaml`.

2. Adding `import-linter` as a pre-commit hook catches violations at commit time,
   reducing the feedback loop from "push → CI fail → fix → push" to "commit → fail → fix".

3. The hook should run `lint-imports` against the contracts in `pyproject.toml`.

4. Verify the hook works by temporarily introducing a boundary violation and confirming
   the hook catches it.

### Acceptance Criteria

1. `.pre-commit-config.yaml` includes an `import-linter` hook.
2. `pre-commit run --all-files` passes with the new hook.
3. Boundary violations are caught at commit time (verified by test).
4. Full gate suite passes.

### Files to Create/Modify

- Modify: `.pre-commit-config.yaml`

---

## T38.4 — Documentation & Hygiene Polish Batch

**Priority**: P2 — Cosmetic. Batched per Rule 16 (materiality threshold).

### Context & Constraints

1. **CHANGELOG footer stale**: References Phase 36 commit `9b51e14` instead of
   Phase 37 commit `72d3007`. Update to "Phase 0.8 through Phase 37".

2. **DEVELOPER_GUIDE.md line 9 stale**: Says "Phase 30" — should reference Phase 37.

3. **request_limits.py:231 broad ValueError**: Narrow the try/except to only the
   Content-Length parsing line, not the entire block. Currently catches unintended
   exceptions from downstream code.

4. **job_finalization.py:96 silent signing key failure**: When `ARTIFACT_SIGNING_KEY`
   fails `bytes.fromhex()`, the function returns None (skips verification) with only
   a WARNING log. Upgrade to ERROR level for production visibility.

### Acceptance Criteria

1. CHANGELOG footer references Phase 37 and commit `72d3007`.
2. DEVELOPER_GUIDE.md references Phase 37.
3. `request_limits.py` narrows the ValueError try/except to only the parsing line.
4. `job_finalization.py` logs at ERROR (not WARNING) when signing key parse fails.
5. Full gate suite passes.

### Files to Create/Modify

- Modify: `CHANGELOG.md`
- Modify: `docs/DEVELOPER_GUIDE.md`
- Modify: `src/synth_engine/bootstrapper/dependencies/request_limits.py`
- Modify: `src/synth_engine/modules/synthesizer/job_finalization.py`

---

## Task Execution Order

```
T38.1 (Fix audit failure) ──────────> sequential (correctness first)
T38.2 (Timing side-channel) ────────> after T38.1 (both touch security)
T38.3 (Import-linter hook) ─────────> parallel with T38.1/T38.2
T38.4 (Polish batch) ───────────────> parallel with T38.1/T38.2
```

T38.1 must complete first (critical correctness fix). T38.3 and T38.4 are independent.

---

## Phase 38 Exit Criteria

1. Audit failure after budget deduction marks job FAILED (not silent completion).
2. Vault unseal timing is constant regardless of passphrase content.
3. Import-linter enforced at pre-commit time.
4. Documentation references current (Phase 37).
5. All quality gates pass.
6. Zero open advisories in RETRO_LOG.
7. Review agents pass for all tasks.
