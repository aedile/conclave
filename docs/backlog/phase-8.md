# Phase 8 — Advisory Drain Sprint

**Goal**: Clear ALL 16 open advisories to zero. No new feature work.
This phase is a user-directed advisory drain sprint (2026-03-16).

**Prerequisite**: Phase 7 must be complete (all tasks merged).

---

## T8.1 — Integration Test Gaps (ADV-021, ADV-064)

**Priority**: Start here — low risk, high confidence.

### Context & Constraints

- **ADV-021** (QA P2-D2, DEFERRED): `EncryptedString` NULL passthrough, empty-string, and
  unicode/multi-byte PII paths are not exercised at the integration level. Only unit-tested.
  Write integration tests in `tests/integration/` that exercise these edge cases through the
  real SQLAlchemy type decorator against an async SQLite database.
- **ADV-064** (QA P6-T6.2, ADVISORY): `except (UnicodeDecodeError, ValueError)` branch in
  `RequestBodyLimitMiddleware` cannot be directly hit because `bytes.decode(errors="replace")`
  never raises `UnicodeDecodeError`. Branch is defensive resilience code. Decision required:
  either remove the unreachable branch (dead code) or replace `errors="replace"` with
  `errors="strict"` so the branch becomes reachable and testable. If removing, ensure no
  behavioral regression.

### Acceptance Criteria

1. Integration tests for `EncryptedString`: NULL round-trip, empty-string round-trip,
   unicode/multi-byte PII (e.g., CJK characters) round-trip through the type decorator
   against a real async database session.
2. `RequestBodyLimitMiddleware` unreachable branch resolved: either removed (with test
   confirming no regression) or made reachable (with test exercising it).
3. ADV-021 and ADV-064 drained from RETRO_LOG Open Advisory Items table.

### Testing & Quality Gates

- `poetry run pytest tests/unit/ --cov=src/synth_engine --cov-fail-under=90 -W error`
- `poetry run pytest tests/integration/ -v --no-cov`
- All review agents spawned.

---

## T8.2 — Security Hardening (ADV-040, ADV-057, ADV-058, ADV-067)

**Priority**: Security is Priority 0. Address these next.

### Context & Constraints

- **ADV-040** (DevOps T4.2b, DEFERRED): Pickle-based `ModelArtifact` persistence uses
  `# nosec B301/B403`. Add HMAC-SHA256 signing to pickle artifacts so that only
  self-produced artifacts are trusted on deserialization. Use the existing `AUDIT_KEY`
  or a dedicated artifact signing key.
- **ADV-057** (DevOps T5.3, DEFERRED): Production source-map emission (`sourcemap: true`
  in `vite.config.ts`). Set `sourcemap: false` for production builds. Dev builds can keep
  source maps via `vite.config.ts` mode detection.
- **ADV-058** (DevOps T5.3, ADVISORY): vitest's esbuild CVE (GHSA-67mh-4wv8-2f99).
  Pin `esbuild >=0.25.0` via npm overrides in `package.json`.
- **ADV-067** (DevOps P7-T7.3, ADVISORY): `PrivacyEngine(secure_rng=True)` evaluation.
  If Opacus supports it without breaking tests, enable it. If it causes performance or
  compatibility issues, document the decision in an ADR amendment.

### Acceptance Criteria

1. `ModelArtifact` pickle serialization includes HMAC-SHA256 signature. Deserialization
   verifies HMAC before unpickling. `# nosec` comments updated with HMAC justification.
2. `vite.config.ts` emits `sourcemap: false` in production mode.
3. `package.json` includes `esbuild >=0.25.0` override. `npm audit` passes.
4. `PrivacyEngine(secure_rng=True)` evaluated: either enabled with passing tests, or
   documented decision to defer with ADR amendment.
5. ADV-040, ADV-057, ADV-058, ADV-067 drained.

### Testing & Quality Gates

- Unit tests for HMAC signing/verification of ModelArtifact.
- `poetry run pytest tests/unit/ --cov=src/synth_engine --cov-fail-under=90 -W error`
- `npm audit` in frontend directory.
- All review agents spawned.

---

## T8.3 — Data Model & Architecture Cleanup (ADV-050, ADV-054, ADV-071)

**Priority**: Architecture debt — clean module boundaries.

### Context & Constraints

- **ADV-050** (Arch T4.4, DEFERRED): `Float` column type for epsilon ledger columns.
  Replace with `Numeric(precision=20, scale=10)` or `DECIMAL` to prevent floating-point
  accumulation drift. Requires migration if Alembic is available (see T8.4), otherwise
  update the SQLModel definition and document the migration path.
- **ADV-054** (Arch T5.2, DEFERRED): `LicenseError.status_code` embeds HTTP semantics.
  Remove `status_code` attribute from `LicenseError`. Move HTTP status mapping to the
  bootstrapper/middleware layer where framework concerns belong (per ADR-0008).
- **ADV-071** (Arch P7-T7.5, ADVISORY): Re-export `BudgetExhaustionError` from
  `modules/privacy/__init__.py` for stable public API surface.

### Acceptance Criteria

1. `PrivacyLedger` epsilon columns use `Numeric` type instead of `Float`.
2. `LicenseError` no longer has `status_code` attribute. HTTP mapping in bootstrapper.
3. `BudgetExhaustionError` importable from `synth_engine.modules.privacy`.
4. ADV-050, ADV-054, ADV-071 drained.

### Testing & Quality Gates

- `poetry run pytest tests/unit/ --cov=src/synth_engine --cov-fail-under=90 -W error`
- `poetry run pytest tests/integration/ -v --no-cov`
- `poetry run mypy src/` must pass with new Numeric types.
- All review agents spawned.

---

## T8.4 — CI Infrastructure (ADV-052, ADV-062, ADV-065, ADV-066, ADV-069)

**Priority**: CI health and enforcement.

### Context & Constraints

- **ADV-052** (DevOps T5.1, DEFERRED): No Alembic migration for `connection`/`setting`
  tables. Initialize Alembic, create initial migration for all existing tables.
- **ADV-062** (DevOps T6.1, ADVISORY): E2E CI job rebuilds frontend twice. Add
  build-artifact handoff between `frontend` and `e2e` CI jobs.
- **ADV-065** (DevOps P6-T6.2, ADVISORY): `zap_test.db` cleanup in ZAP CI job.
- **ADV-066** (QA P6-T6.3, ADVISORY): Add `pytest -W error` to ci.yml and ci-local.sh.
- **ADV-069** (DevOps P7-T7.5, ADVISORY): Marker-based synthesizer test routing.

### Acceptance Criteria

1. Alembic initialized with `alembic init`. Initial migration covers all existing tables.
2. Frontend build artifact shared between CI jobs (no double-build).
3. ZAP CI job cleans up `zap_test.db` after completion.
4. `pytest -W error` added to both CI environments.
5. Synthesizer tests routed via `pytest -m synthesizer` marker.
6. ADV-052, ADV-062, ADV-065, ADV-066, ADV-069 drained.

### Testing & Quality Gates

- CI pipeline must pass end-to-end after changes.
- `poetry run pytest tests/unit/ --cov=src/synth_engine --cov-fail-under=90 -W error`
- All review agents spawned.

---

## T8.5 — Documentation & Operator Gaps (ADV-070, ADV-072)

**Priority**: Final cleanup.

### Context & Constraints

- **ADV-070** (DevOps P7-T7.5, ADVISORY): `FORCE_CPU` env var undocumented in
  `.env.example`. Add with descriptive comment.
- **ADV-072** (UI/UX P7-T7.5, ADVISORY): DP parameter dashboard accessibility plan.
  Since no dashboard work is in scope, create a design note documenting the required
  `aria-describedby` patterns for future DP parameter inputs. File in `docs/adr/` as
  an ADR amendment or design note.

### Acceptance Criteria

1. `.env.example` includes `FORCE_CPU` with descriptive comment.
2. Design note for DP parameter accessibility documented.
3. ADV-070 and ADV-072 drained.
4. Final advisory count: **0**.

### Testing & Quality Gates

- No code changes expected — docs-gate applies.
- All review agents spawned.

---

## Phase 8 Exit Criteria

- All 16 advisories drained from RETRO_LOG Open Advisory Items table.
- All quality gates passing.
- Phase 8 end-of-phase retrospective completed.
- Open advisory count: **0**.
