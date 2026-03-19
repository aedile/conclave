# Phase 29 — Documentation Integrity & Review Debt

**Goal**: Correct user-facing claims that overstate the current DP implementation, close
documentation gaps identified in the post-Phase 27 retrospective roast, and address minor
hardening items. No new features. This phase is explicitly preparatory work for Phase 30
(True Discriminator-Level DP-SGD), ensuring the README and operator docs accurately describe
what the system does *today* before Phase 30 changes what the system does *tomorrow*.

**Prerequisite**: Phase 28 must be complete (all tasks merged, retrospective signed off).

**Source**: Post-Phase 27 comprehensive 5-perspective retrospective roast.

---

## T29.1 — README DP Claim Correction

**Priority**: P0 — Documentation integrity (Constitution Priority 6). User-facing claims must
match the implemented behavior. A compliance officer reading the README would currently
conclude that Conclave provides formal end-to-end differential privacy guarantees on synthetic
output. The current implementation provides DP-SGD accounting on a proxy linear model, not on
the CTGAN Discriminator that generates the synthetic data. This is honest engineering (documented
in ADR-0025 §T7.3) but the README does not surface the distinction.

### Context & Constraints

1. **Current README claim** (line 67-68): "Real per-sample gradient clipping and Gaussian noise
   injection provide mathematically provable differential privacy guarantees."
   This is accurate for the proxy model that Opacus wraps. It is NOT accurate for the
   CTGAN Discriminator→Generator pipeline that actually produces synthetic data.

2. **ADR-0025 is honest**: The ADR explicitly describes this as "a practical approximation"
   and documents the proxy model approach, its limitations, and the rationale. The gap is
   between the ADR (internal, honest) and the README (external, overstated).

3. **The DP Quality Report** (`docs/DP_QUALITY_REPORT.md`) may reference epsilon values from
   the proxy model without clarifying the measurement methodology. Must be reviewed and
   corrected if affected.

4. The correction must not downplay the system's real capabilities. The masking pipeline,
   privacy budget accountant, HMAC-sealed artifacts, and WORM audit log are all genuine
   security controls. The DP synthesis path provides real Opacus accounting — the limitation
   is that the accounting is measured on a proxy model, not the GAN discriminator.

5. The README should include a forward reference to Phase 30 (True Discriminator-Level DP-SGD)
   as a planned enhancement, so readers understand the roadmap.

### Acceptance Criteria

1. README "DP-CTGAN Training" paragraph rewritten to accurately describe the proxy-model
   approach: "Opacus DP-SGD accounting is performed on a proxy linear model trained on the
   same preprocessed data as the CTGAN. The epsilon budget reflects real gradient-step
   accounting proportional to the dataset. Phase 30 will apply DP-SGD directly to the CTGAN
   Discriminator for end-to-end differential privacy."
2. A new "DP Maturity" section in the README (or amendment to the existing "Security" table)
   that clearly states: "Current: proxy-model epsilon accounting. Planned: discriminator-level
   DP-SGD (Phase 30)."
3. `docs/DP_QUALITY_REPORT.md` reviewed — any epsilon values annotated with
   "(proxy-model measurement)" where applicable.
4. No changes to source code in this task — documentation only.

### Testing & Quality Gates

- `pre-commit run --all-files` — must pass (doc formatting).
- All review agents spawned (QA + DevOps — documentation review).
- README rendered correctly via `gh repo view` or local Markdown preview.

### Files to Create/Modify

- `README.md`
- `docs/DP_QUALITY_REPORT.md` (if affected)

---

## T29.2 — Frontend `node_modules` Gitignore Audit

**Priority**: P1 — Repository hygiene. If `frontend/node_modules/` is committed to git,
this adds massive blob weight to every clone. If intentional for air-gap, document the
rationale; if accidental, add to `.gitignore` and purge from history.

### Context & Constraints

1. The `frontend/` directory contains a `node_modules/` tree visible in the file listing.
   Determine whether this is (a) committed to git, or (b) a local untracked artifact.

2. If committed: Air-gap deployments may require vendored dependencies. If this is the
   rationale, add a comment to `.gitignore` explaining the intentional inclusion and
   consider using `npm pack` tarballs instead of raw `node_modules/` (smaller, deterministic).

3. If untracked: Confirm `.gitignore` already covers it. No action needed.

### Acceptance Criteria

1. `git ls-files frontend/node_modules/ | wc -l` returns 0 (not committed), OR:
2. If committed intentionally: ADR documenting the air-gap vendoring decision, `.gitignore`
   comment, and recommendation to migrate to `npm pack` tarballs in a future phase.
3. If committed accidentally: added to `.gitignore`, removed from git tracking via
   `git rm -r --cached frontend/node_modules/`, and `.gitignore` entry verified.

### Testing & Quality Gates

- `git status` clean after changes.
- `pre-commit run --all-files` passes.
- Frontend `npm ci && npm run build` still succeeds (no broken dependency references).

### Files to Create/Modify

- `.gitignore` (if needed)
- `docs/adr/ADR-0035-frontend-dependency-vendoring.md` (if intentional)

---

## T29.3 — Error Message Audience Differentiation

**Priority**: P2 — UX (Constitution Priority 9). Backend error class names
(`BudgetExhaustionError`, `VaultSealedError`) are developer-facing identifiers. When these
surface in the React SPA's RFC 7807 Toast component, a compliance officer or operator sees
jargon instead of actionable guidance.

### Context & Constraints

1. The `RFC7807Toast` component renders `detail` from RFC 7807 error responses. The `detail`
   field currently contains the Python exception message verbatim (e.g.,
   `"DP budget exhausted: epsilon_spent=1.234 >= allocated_epsilon=1.0"`).

2. The error handler in `bootstrapper/errors.py` maps exceptions to RFC 7807 responses. This
   is the correct place to add operator-friendly `title` and `detail` fields that differ from
   the internal exception message.

3. The `title` field should use plain language (e.g., "Privacy Budget Exceeded" instead of
   "BudgetExhaustionError"). The `detail` field should include a remediation action
   (e.g., "Reset the privacy budget via POST /privacy/budget/reset or contact your
   administrator.").

4. The internal exception message must remain unchanged for developer logging — the
   operator-friendly message is a *presentation layer* concern, not a domain concern.

### Acceptance Criteria

1. `bootstrapper/errors.py` (or a new `error_messages.py` mapping) provides operator-friendly
   `title` and `detail` for at least these exceptions:
   - `BudgetExhaustionError` → "Privacy Budget Exceeded" + remediation
   - `VaultSealedError` → "Vault Is Sealed" + "Unseal the vault before performing data operations."
   - `VaultEmptyPassphraseError` → "Empty Passphrase" + "Enter a non-empty passphrase."
   - `VaultConfigError` → "Vault Configuration Error" + remediation pointing to env var setup
2. Internal log messages still contain the full technical exception message (unchanged).
3. RFC 7807 responses include both `title` (human-friendly) and `type` (machine-friendly URI
   or error code) fields.
4. Unit tests verify the mapping for each exception class.
5. Frontend `RFC7807Toast` renders the human-friendly `title` as the heading and `detail` as
   the body — no changes to the component if it already reads these fields.

### Testing & Quality Gates

- `poetry run pytest tests/unit/ --cov=src/synth_engine --cov-fail-under=90 -W error`
- `pre-commit run --all-files`
- Frontend unit tests pass (if RFC7807Toast rendering changes).
- All review agents spawned (QA + DevOps + UI/UX — error message changes touch UX).

### Files to Create/Modify

- `src/synth_engine/bootstrapper/errors.py`
- `tests/unit/test_bootstrapper_errors.py`
- `frontend/src/components/RFC7807Toast.tsx` (only if rendering logic needs adjustment)

---

## T29.4 — Coverage Threshold Elevation to 95%

**Priority**: P2 — Quality gate tightening (Constitution Priority 4). For a security-critical
PII-handling system, 90% is adequate; 95% is appropriate for the maturity level this codebase
has reached.

### Context & Constraints

1. Current coverage is ~96% (reported in Phase 26 retro). The threshold in `pyproject.toml`
   is 90%. Raising the *gate* to 95% codifies the achieved level and prevents regression.

2. Any uncovered lines should be audited. Lines behind `pragma: no cover` must have written
   justification (already required by project conventions).

3. If any modules are currently between 90-95%, they must be brought up to 95% before the
   gate is raised — do not raise the gate and break CI.

### Acceptance Criteria

1. `pyproject.toml` `--cov-fail-under` updated from `90` to `95`.
2. CLAUDE.md quality gate reference updated from 90% to 95%.
3. CONSTITUTION.md enforcement table row updated from 90%+ to 95%+.
4. All existing tests pass at the new threshold.
5. If any module is below 95%, targeted tests added to bring it above the threshold.

### Testing & Quality Gates

- `poetry run pytest tests/unit/ --cov=src/synth_engine --cov-fail-under=95 -W error` — THE gate.
- `pre-commit run --all-files`
- All review agents spawned (QA + DevOps).

### Files to Create/Modify

- `pyproject.toml`
- `CLAUDE.md`
- `CONSTITUTION.md`
- Tests as needed to reach 95% floor.

---

## T29.5 — ADR-0025 Amendment: Proxy Model Limitation & Phase 30 Plan

**Priority**: P1 — Architectural documentation. ADR-0025 documents the proxy model approach
but does not explicitly flag it as a *temporary* architectural compromise with a planned
resolution. This task adds a "Planned Resolution" section to ADR-0025 that cross-references
Phase 30.

### Context & Constraints

1. ADR-0025 currently says "a practical approximation accepted in ADR-0025 §T7.3" but does
   not classify this as temporary vs permanent.

2. Phase 30 will implement true Discriminator-level DP-SGD. ADR-0025 should be amended to
   document: (a) this is a known limitation, (b) Phase 30 is the planned resolution,
   (c) the proxy model approach remains available as a fallback for environments where
   Opacus cannot instrument the CTGAN Discriminator layers.

### Acceptance Criteria

1. ADR-0025 amended with a "Planned Resolution" section documenting the Phase 30 path.
2. ADR-0025 status updated to "Accepted — Superseded by Phase 30 when implemented."
3. Cross-reference to `docs/backlog/phase-30.md`.

### Testing & Quality Gates

- `pre-commit run --all-files`
- All review agents spawned (Architecture review — ADR amendment).

### Files to Create/Modify

- `docs/adr/ADR-0025-custom-ctgan-training-loop.md`

---

## Phase 29 Exit Criteria

1. README accurately describes the current DP implementation without overstating guarantees.
2. `node_modules` status resolved (removed from git or documented as intentional).
3. Operator-facing error messages are human-readable with remediation guidance.
4. Coverage gate raised to 95% and all tests pass.
5. ADR-0025 amended with Phase 30 forward reference.
6. All advisory items drained or documented.
7. Phase 30 backlog document exists and is ready for execution.
