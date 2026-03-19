# Phase 36 — Configuration Centralization, Documentation Pruning & Hygiene

**Goal**: Centralize the scattered environment variable reads into a validated Pydantic
settings model, prune ~3,500 lines of stale/boilerplate documentation, standardize module
exports and logging, and close remaining edge-case test gaps identified in the audit.

**Prerequisite**: Phase 35 merged. Zero open advisories.

**ADR**: None required — no architectural decisions, only consolidation and cleanup.

**Source**: Production Readiness Audit, 2026-03-18 — Refactor Priorities P1/P2.

---

## T36.1 — Centralize Configuration Into Pydantic Settings Model

**Priority**: P1 — Environment variables are currently read via `os.environ.get()` in 8+
files with inconsistent timing (some at import, some at runtime, some at unseal time).

### Context & Constraints

1. Current configuration access is scattered:
   - `bootstrapper/dependencies/db.py:42` — reads `DATABASE_URL` at dependency resolution
   - `modules/synthesizer/storage.py:76` — reads `FORCE_CPU` on every call (deferred)
   - `shared/security/vault.py:136` — reads `VAULT_SEAL_SALT` only at unseal time
   - `bootstrapper/cli.py:83` — reads `MASKING_SALT` with hardcoded fallback
   - `modules/synthesizer/job_finalization.py:28` — reads `ARTIFACT_SIGNING_KEY`
   - `bootstrapper/config_validation.py` — validates a subset at startup
   - Plus `OTEL_EXPORTER_OTLP_ENDPOINT`, `CONCLAVE_ENV`, `CONCLAVE_SSL_REQUIRED`, etc.

2. A new developer must grep the entire codebase to discover all env vars. The
   `.env.example` file lists them but there is no programmatic validation that the
   example file matches what the code actually reads.

3. Proposed approach:
   - Create `src/synth_engine/shared/settings.py` with a Pydantic `BaseSettings` model.
   - All env vars declared as typed fields with defaults and validators.
   - Single `get_settings()` function with `@lru_cache` for singleton access.
   - Modules receive settings via DI (FastAPI `Depends`) or explicit parameter passing.
   - `config_validation.py` logic migrates into Pydantic validators.

4. Vault-specific values (`VAULT_SEAL_SALT`) that are intentionally deferred (read at
   unseal time, not boot) must remain deferred. The settings model should have a
   `vault` sub-model that is populated lazily or on demand, not at import time.

5. `.env.example` should be generated from or validated against the settings model
   (a pre-commit hook or CI check).

### Acceptance Criteria

1. `shared/settings.py` defines a `ConclaveSettings(BaseSettings)` model with all env vars.
2. All `os.environ.get()` calls in `src/` replaced with settings model access.
3. `config_validation.py` startup checks migrated to Pydantic validators.
4. `.env.example` matches the settings model fields (CI check or pre-commit hook).
5. Vault deferred reads preserved — `VAULT_SEAL_SALT` not read at boot.
6. Full gate suite passes.

### Testing & Quality Gates

- New test: construct settings with invalid values, verify `ValidationError` raised.
- New test: construct settings with missing required fields in production mode, verify fail.
- Existing config_validation tests updated to use settings model.
- QA + Architecture reviewers spawned.

### Files to Create/Modify

- Create: `src/synth_engine/shared/settings.py`
- Modify: `src/synth_engine/bootstrapper/dependencies/db.py`
- Modify: `src/synth_engine/bootstrapper/config_validation.py`
- Modify: `src/synth_engine/bootstrapper/cli.py`
- Modify: `src/synth_engine/modules/synthesizer/storage.py`
- Modify: `src/synth_engine/modules/synthesizer/job_finalization.py`
- Modify: `src/synth_engine/shared/security/vault.py`
- Modify: `src/synth_engine/shared/telemetry.py`
- Create: `tests/unit/test_settings.py`

---

## T36.2 — Split `bootstrapper/errors.py` Into Focused Modules

**Priority**: P1 — At 449 lines with 4 distinct concerns (RFC 7807 formatting, operator
error mappings, ASGI middleware, JSON sanitization), this file violates single-responsibility.

### Context & Constraints

1. Current `errors.py` contains:
   - `problem_detail()` and `operator_error_response()` — RFC 7807 dict builders
   - `OPERATOR_ERROR_MAP` — exception-to-HTTP-status mappings (expanded in Phase 34)
   - `RFC7807Middleware` — ASGI middleware class (~150 lines)
   - `_sanitize_for_json()` — recursive NaN/Infinity sanitizer

2. Proposed split:
   - `bootstrapper/errors/formatter.py` — `problem_detail()`, `operator_error_response()`
   - `bootstrapper/errors/middleware.py` — `RFC7807Middleware`
   - `bootstrapper/errors/mapping.py` — `OPERATOR_ERROR_MAP`
   - `bootstrapper/errors/__init__.py` — re-exports public API

3. `_sanitize_for_json()` moves into `formatter.py` (it's a formatting concern).

4. Import paths must remain stable — `from synth_engine.bootstrapper.errors import ...`
   must continue to work via `__init__.py` re-exports.

### Acceptance Criteria

1. `bootstrapper/errors.py` replaced by `bootstrapper/errors/` package.
2. No file in the package exceeds 200 lines.
3. All existing imports of `bootstrapper.errors` work unchanged (re-exports in `__init__.py`).
4. Full gate suite passes.

### Testing & Quality Gates

- All existing error tests pass unchanged (they import from `bootstrapper.errors`).
- QA reviewer spawned.

### Files to Create/Modify

- Delete: `src/synth_engine/bootstrapper/errors.py`
- Create: `src/synth_engine/bootstrapper/errors/__init__.py`
- Create: `src/synth_engine/bootstrapper/errors/formatter.py`
- Create: `src/synth_engine/bootstrapper/errors/middleware.py`
- Create: `src/synth_engine/bootstrapper/errors/mapping.py`

---

## T36.3 — Documentation Pruning & Credibility Fixes

**Priority**: P1 — README contradicts DP_QUALITY_REPORT; BUSINESS_REQUIREMENTS.md is
257 lines of AI-generated boilerplate; `docs/retired/` clutters the index.

### Context & Constraints

1. **README vs DP_QUALITY_REPORT contradiction**: README:157-170 claims DP-SGD Phase 30 is
   complete. DP_QUALITY_REPORT.md:62,68-72 shows "placeholder" and "pending benchmark run".
   Either populate the benchmarks or add a clear "Benchmarks Pending" callout in README.

2. **BUSINESS_REQUIREMENTS.md**: 257 lines with 62 citations to vendor blogs. Zero original
   analysis. Replace with a 1-2 paragraph executive summary of the business need.

3. **`docs/retired/`**: Contains 6+ superseded documents still indexed in `docs/index.md`.
   Delete or move to `docs/archive/` and remove from the main index.

4. **Retrospective archives**: `docs/retro_archive/phases-0-to-7.md` and
   `phases-8-to-14.md` should be consolidated or clearly separated from the active
   `RETRO_LOG.md` in the index.

5. **ADR lifecycle gap**: RETRO_LOG line 42-43 notes ADRs are not amended when subject code
   is removed. Add a checklist item to the architecture review prompt: "If this diff removes
   code covered by an ADR, is the ADR amended?"

### Acceptance Criteria

1. `BUSINESS_REQUIREMENTS.md` replaced with concise (under 50 lines) executive summary.
2. `DP_QUALITY_REPORT.md` either has real benchmarks or has a prominent "Pending" banner
   and README:157-170 updated to match.
3. `docs/retired/` contents deleted or moved to `docs/archive/` (not indexed).
4. `docs/index.md` updated — removed entries for retired/archived docs.
5. Architecture review prompt (`docs/prompts/review/ARCHITECTURE.md`) includes ADR
   amendment check.
6. Markdownlint passes on all modified docs.

### Testing & Quality Gates

- `markdownlint` on all modified files.
- Manual verification that `docs/index.md` links resolve.
- QA reviewer spawned.

### Files to Create/Modify

- Rewrite: `docs/BUSINESS_REQUIREMENTS.md`
- Modify: `docs/DP_QUALITY_REPORT.md` (populate or mark pending)
- Modify: `README.md` (align DP claims with reality)
- Delete or move: `docs/retired/*`
- Modify: `docs/index.md`
- Modify: `docs/prompts/review/ARCHITECTURE.md`

---

## T36.4 — Standardize Module Exports, Logging, and Missing Edge-Case Tests

**Priority**: P2 — Consistency hygiene and test gap closure.

### Context & Constraints

1. **Missing `__all__`**: `modules/ingestion/__init__.py` and `modules/masking/__init__.py`
   have no `__all__` definition. All other modules define it explicitly.

2. **Logger naming drift**: `modules/subsetting/egress.py:57` uses `logger` instead of
   the project-wide `_logger` convention.

3. **Missing edge-case tests** identified in audit:
   - Masking: no test for malformed salt (empty string, None, null bytes, special chars)
   - Masking: no test for concurrent `mask_value()` calls (thread-safety advisory)
   - HMAC signing: no test for empty key, empty data, key length boundaries
   - Vault: no test for very long passphrase (>1MB)
   - Privacy accountant: no test for NUMERIC(20,10) precision loss at rounding boundary

4. These are individually small but collectively close gaps that could produce silent
   failures in production edge cases.

### Acceptance Criteria

1. `modules/ingestion/__init__.py` defines `__all__`.
2. `modules/masking/__init__.py` defines `__all__`.
3. `modules/subsetting/egress.py:57` changed from `logger` to `_logger`.
4. At least 3 new masking edge-case tests (empty salt, None salt, special char salt).
5. At least 2 new HMAC signing edge-case tests (empty key, empty data).
6. At least 1 vault edge-case test (very long passphrase).
7. At least 1 privacy accountant precision test (value that rounds to 0 in NUMERIC(20,10)).
8. Full gate suite passes.

### Testing & Quality Gates

- This IS primarily a testing task.
- QA reviewer spawned.

### Files to Create/Modify

- Modify: `src/synth_engine/modules/ingestion/__init__.py`
- Modify: `src/synth_engine/modules/masking/__init__.py`
- Modify: `src/synth_engine/modules/subsetting/egress.py` (logger rename)
- Modify: `tests/unit/test_masking_algorithms.py` (edge-case tests)
- Modify: `tests/unit/test_hmac_signing.py` (edge-case tests)
- Modify: `tests/unit/test_vault.py` (long passphrase test)
- Modify: `tests/unit/test_privacy_accountant.py` (precision test)

---

## T36.5 — Full E2E Demo Run With Production-Worthy Dataset & Screenshots

**Priority**: P0 — Final validation. The project's credibility rests on demonstrating the
complete pipeline works end-to-end with a realistic dataset, not just unit test fixtures.

### Context & Constraints

1. This is the **capstone task** of the last scheduled phase. It exercises the entire system
   top-to-bottom with a load-worthy dataset and produces screenshot evidence for documentation.

2. The demo must use a realistic multi-table PostgreSQL schema (not the 5-row test fixtures).
   Use the `sample_data/` directory or seed a database with at least 1,000 rows across 5+
   tables with FK relationships.

3. The demo exercises every pipeline stage in sequence:
   - **Vault unseal** — screenshot of the unseal UI
   - **Database connection** — connect to the seeded PostgreSQL instance
   - **Schema reflection** — verify tables and FK relationships detected
   - **Masking** — run deterministic FPE masking, verify output
   - **Subsetting** — extract FK-consistent subset, verify row counts
   - **Synthesis** — run DP-SGD CTGAN (or proxy model) on a table, verify output shape
   - **Privacy budget** — verify epsilon was decremented
   - **Download** — download Parquet artifact, verify HMAC signature
   - **Dashboard** — screenshot of the job dashboard showing completed job
   - **Audit trail** — verify WORM audit entries exist

4. Every stage must have a **screenshot** captured and saved to `docs/screenshots/`.
   Overwrite any existing screenshots from prior E2E validation runs.

5. The existing `docs/E2E_VALIDATION.md` (if present) must be **overwritten** with the
   new demo results — timestamps, row counts, epsilon values, screenshot references.

6. If the frontend is running (via `npm run dev` or Docker), capture frontend screenshots.
   If not, capture API response screenshots (curl output or httpie).

7. This task is documentation + validation, not code changes. The only files created/modified
   are in `docs/`.

### Acceptance Criteria

1. Full pipeline executed end-to-end with ≥1,000 source rows.
2. Every pipeline stage documented with screenshot or terminal output evidence.
3. `docs/E2E_VALIDATION.md` overwritten with current demo results.
4. `docs/screenshots/` contains current screenshots (not stale).
5. Masking output shows correct per-column FPE masking (not full names in first_name columns).
6. Privacy budget shows correct epsilon decrement.
7. HMAC signature verification passes on downloaded artifact.
8. `pre-commit run --all-files` passes.

### Testing & Quality Gates

- This IS the validation task — it validates the entire system.
- QA + DevOps reviewers spawned to verify evidence accuracy.

### Files to Create/Modify

- Overwrite: `docs/E2E_VALIDATION.md`
- Overwrite: `docs/screenshots/*.png` (or create if not present)

---

## Task Execution Order

```
T36.1 (Pydantic settings) ──────────────────────> parallel
T36.2 (errors.py split) ────────────────────────> parallel
T36.3 (Documentation pruning) ──────────────────> parallel
T36.4 (Exports, logging, edge-case tests) ──────> parallel
                                    all above ──> T36.5 (Full E2E demo)
```

T36.1–T36.4 are independent. T36.5 must run LAST after all code changes are complete,
as it validates the final state of the system.

---

## Phase 36 Exit Criteria

1. All environment variable reads centralized in Pydantic settings model.
2. `bootstrapper/errors.py` decomposed into focused modules.
3. Documentation pruned — no stale boilerplate, accurate numbers.
4. Module exports standardized, logging convention enforced, edge-case gaps closed.
5. Full E2E demo completed with ≥1,000 rows, all stages screenshotted.
6. `docs/E2E_VALIDATION.md` current with Phase 36 demo results.
7. All quality gates pass.
8. Zero open advisories in RETRO_LOG.
9. Review agents pass for all tasks.
