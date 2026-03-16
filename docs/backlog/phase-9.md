# Phase 9 — Production Hardening & Correctness Sprint

**Goal**: Harden the codebase for production readiness. Drain remaining 5 advisories,
strengthen operational infrastructure, and close correctness gaps identified in the
Phase 8 retrospective. No new user-facing features.

**Prerequisite**: Phase 8 must be complete (all tasks merged, retrospective signed off).

---

## T9.1 — Advisory Drain + Startup Validation (ADV-073, ADV-074, ADV-075, ADV-076, ADV-077)

**Priority**: Start here — clears all remaining advisories and adds fail-fast boot checks.

### Context & Constraints

- **ADV-073** (DevOps P8-T8.4): Synthesizer integration test files carry only
  `pytest.mark.synthesizer`, not dual `[pytest.mark.integration, pytest.mark.synthesizer]`.
  Add the missing `pytest.mark.integration` marker to `test_synthesizer_integration.py` and
  `test_dp_training_integration.py` for consistency with `test_e2e_dp_synthesis.py`.
- **ADV-074** (QA P8-T8.3): `spend_budget(amount=1e-11)` produces `Decimal("1.1e-11")` via
  `Decimal(str(float))` — scientific-notation edge case not tested. Add a parametrized test
  case with `amount=1e-11` to `test_privacy_accountant.py` documenting the conversion contract.
- **ADV-075** (DevOps P8-T8.3): `_render_qr_code` fallback logs raw Pillow/qrcode exception
  at WARNING level. Replace with `type(exc).__name__` only to prevent internal-path disclosure.
- **ADV-076** (QA P8-T8.2): `ModelArtifact.save()` raises `ValueError` for empty signing_key
  but `load()` raises `SecurityError`. Add `ValueError` guard in `load()` for empty key
  (matching `save()` behavior), and add a test for it.
- **ADV-077** (DevOps P8-T8.2): Add startup configuration validation in the bootstrapper.
  Create a `validate_config()` function that runs at boot and asserts all required env vars
  are present and well-formed. In production mode (`ENV=production` or similar), require
  `ARTIFACT_SIGNING_KEY`, `AUDIT_KEY`, `DATABASE_URL`. Fail fast with clear error messages.

### Acceptance Criteria

1. All 5 integration test files with synthesizer tests carry dual markers.
2. `test_spend_budget_scientific_notation_decimal` test passes for `amount=1e-11`.
3. `_render_qr_code` fallback logs `type(exc).__name__` only, not raw exception.
4. `ModelArtifact.load(path, signing_key=b"")` raises `ValueError` (not `SecurityError`).
5. `validate_config()` runs at startup; missing required env vars raise `SystemExit` with
   clear error message before the server accepts traffic.
6. All 5 ADV rows drained from RETRO_LOG. Open advisory count: **0**.

### Testing & Quality Gates

- `poetry run pytest tests/unit/ --cov=src/synth_engine --cov-fail-under=90 -W error`
- `poetry run pytest tests/integration/ -v --no-cov`
- All review agents spawned.

---

## T9.2 — Operator Manual Refresh

**Priority**: Documentation currency — Constitution Priority 6.

### Context & Constraints

The OPERATOR_MANUAL.md was last substantively updated during Phase 5. Phases 6–8 introduced:
- HMAC artifact signing (ARTIFACT_SIGNING_KEY configuration)
- Alembic database migrations (alembic upgrade head workflow)
- FORCE_CPU environment variable
- Marker-based CI test routing
- Differential Privacy configuration (epsilon, noise_multiplier calibration)
- Zero-warning pytest policy via pyproject.toml filterwarnings
- ADR-0017a (Opacus secure_mode decision)

The operator manual must be refreshed to cover all of these.

### Acceptance Criteria

1. OPERATOR_MANUAL.md updated with:
   - ARTIFACT_SIGNING_KEY setup and rotation instructions
   - Alembic migration workflow (`alembic upgrade head` before first run)
   - FORCE_CPU documentation
   - DP configuration guidance (link to DP_QUALITY_REPORT.md)
2. README.md updated if any top-level instructions are stale.

### Testing & Quality Gates

- No code changes expected — docs-gate applies.
- All review agents spawned.

---

## T9.3 — Bootstrapper Decomposition

**Priority**: Architecture — `main.py` at 20K+ bytes violates clean-code principles.

### Context & Constraints

`src/synth_engine/bootstrapper/main.py` is 20,621 bytes and houses router registration,
middleware setup, startup/shutdown hooks, and application factory logic in a single file.
This exceeds reasonable single-file complexity. Decompose into:
- `main.py` — Application factory (`create_app()`) and nothing else
- `routers.py` — Router registration (include_router calls)
- `middleware.py` — Middleware stack setup
- `lifecycle.py` — Startup/shutdown event handlers

Ensure import-linter contracts still pass. No behavioral changes.

### Acceptance Criteria

1. `main.py` reduced to <200 LOC (application factory only).
2. Router registration, middleware setup, and lifecycle hooks in separate files.
3. All existing tests pass without modification (pure refactor).
4. Import-linter contracts pass.

### Testing & Quality Gates

- `poetry run pytest tests/unit/ --cov=src/synth_engine --cov-fail-under=90 -W error`
- `poetry run pytest tests/integration/ -v --no-cov`
- `poetry run lint-imports`
- All review agents spawned.

---

## Phase 9 Exit Criteria

- All 5 remaining advisories drained (open count: 0).
- Operator manual current with Phase 6–8 changes.
- Bootstrapper main.py decomposed below 200 LOC.
- All quality gates passing.
- Phase 9 end-of-phase retrospective completed.
