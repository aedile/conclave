# Changelog

All notable changes to Conclave are documented here. Entries are organized by phase.
Phases correspond to the project's delivery milestones. Each entry references the
primary pull request(s) merged for that phase.

For architectural decisions, see [`docs/adr/`](docs/adr/).
For the full development retrospective, see [`docs/RETRO_LOG.md`](docs/RETRO_LOG.md).
For a narrative account of the project, see [`docs/archive/DEVELOPMENT_STORY.md`](docs/archive/DEVELOPMENT_STORY.md).

---

## Phase 51 — Release Engineering (T51.2)
*2026-03-23 | PR TBD*

- Added `.github/workflows/release.yml`: three-job release pipeline triggered on `v*` tag pushes.
  Jobs: `validate-tag` (semver format check, version export) → `build-release` (Docker image,
  air-gap bundle, CycloneDX SBOM, sha256sums) → `publish-release` (GitHub Release with all assets).
  All six `uses:` references SHA-pinned per T3.5.1 supply-chain hardening. Permissions scoped
  per-job; only `publish-release` has `contents: write` (T51.2).
- Added `tests/unit/test_release_workflow.py`: 21 structural and security tests for the workflow
  YAML covering SHA-pinning, job dependencies, trigger isolation, and artifact flow.

## Phase 50 — Production Security Fixes (in progress)
*2026-03-23 | PR TBD*

- DP budget enforcement changed to fail-closed: `BudgetExhaustionError` and `EpsilonMeasurementError`
  always block synthesis, no silent pass-through (ADR-0050).
- `CONCLAVE_ENV` defaults to `"production"`; fresh deployments boot with auth enforced (T50.3).
- Removed `/security/shred` and `/security/keys/rotate` from `AUTH_EXEMPT_PATHS` (ADV-P47-04).
- TOCTOU in `ModelArtifact.load()` eliminated: replaced `os.path.exists()` pre-check with bounded
  `f.read(_MAX_ARTIFACT_SIZE_BYTES + 1)` and post-read `len(raw)` guard (T50.4, ADR-0052 mutmut
  Python 3.14 gap accepted).

## Documentation Cleanup & Tightening
*2026-03-23 | PR [#180](../../pull/180)*

- Archived 8 historical files to `docs/archive/`: DEVELOPMENT_STORY, BACKLOG, DOCUMENT_INDEX,
  E2E_VALIDATION, DP_QUALITY_REPORT, e2e_load_test_results.json, ARCHITECTURAL_REQUIREMENTS,
  BUSINESS_REQUIREMENTS. All cross-references updated.
- Tightened 15 active documentation files; total active-docs reduction ~9,400 → ~6,400 lines (~32%).
- Every command, config value, code block, security warning, and deployment step preserved.
  Only filler, redundancy, and verbose preambles removed.

## Phase 49 — Test Quality Hardening
*2026-03-23 | PR [#179](../../pull/179)*

- Hardened security-critical assertions: `test_download_hmac_signing.py` 4→20 tests;
  value assertions added to `test_audit.py`, `test_dp_accounting.py`, and `test_ale.py` (T49.1).
- Salt-sensitivity sweeps on all masking functions; parametrized subsetting negative cases
  (mid-stream failure, DB disconnect); settings router value assertions (T49.2).
- Mock reduction: shared `helpers_synthesizer.py` extracted; opt-in `jwt_secret_key_env` fixture;
  2 Opacus integration tests; 3 guardrails edge cases added (T49.3).
- `test_synthesizer_tasks.py` (2738 lines) split into 3 files; all 107 tests preserved (T49.4).
- mutmut 3.x configured for `shared/security/` and `modules/privacy/`; 200 mutants generated,
  0 survived; Python 3.14 SIGSEGV incompatibility accepted in ADR-0047 (T49.5).
- Test metrics: 2466 passed, 1 skipped — coverage 96.76%.

## Advisory Drain — Pre-Phase 49
*2026-03-23 | PR [#178](../../pull/178)*

- Drained 5 advisories (9→4 open): X-Forwarded-For trust model (PRODUCTION_DEPLOYMENT.md
  Appendix B), scope-based auth ADR gap (ADR-0049 written), stale `ale_key` field removed
  from `ConclaveSettings`, Redis INCR+EXPIRE atomicity closed as accepted tradeoff,
  anchor verification equality-only closed as accepted tradeoff.

## Phase 48 — Production-Critical Infrastructure Fixes
*2026-03-23 | PR [#177](../../pull/177)*

- Redis-backed rate limiting replacing the in-process deque fallback; sync Redis pipeline
  dispatched via `asyncio.to_thread()` to avoid blocking the event loop (T48.1).
- Huey worker connection pooling: dedicated async engine per worker, not shared with API
  process; readiness probe reuses shared engine (T48.2, T48.3).
- Audit trail anchoring wired end-to-end: `AuditLogger` → `AnchorManager.maybe_anchor()`
  called on every `log_event()` invocation; Rule 8 wiring gap fixed (T48.4).
- ALE vault enforcement: `ALE_KEY` env-var fallback path removed; all connection encryption
  now requires an unsealed vault (T48.5).
- Review findings resolved: Rule 8 wiring BLOCKER, async dispatch, `docker_secrets.py`
  extraction, stale docstrings, `/ready` engine reuse, `.env.example` anchor settings.

## Phase 47 — Auth & Safety Ops
*2026-03-22 | PR [#174](../../pull/174)*

- Scope enforcement added to security routes (`security:admin`) and settings routes
  (`settings:write`); `AUTH_EXEMPT_PATHS` audited and tightened (T47.1, T47.3).
- `JWT_SECRET_KEY` and `OPERATOR_CREDENTIALS_HASH` added to production-required startup
  validation; bcrypt structural pre-check (`$2b$`, ≥59 chars) prevents hash oracle exposure (T47.4, T47.5).
- Artifact signature hardening: versioned `KEY_ID || HMAC-SHA256` enforced; collect-all
  config validation reports all missing vars in one startup attempt (T47.6).
- Parquet memory bounds, asyncpg TLS 1.3 pin, Redis healthcheck, shutdown cleanup (T47.7, T47.8, T47.10).
- `BudgetExhaustionError` restructured: `str(exc)` always returns a safe generic constant;
  epsilon values stored as typed `Decimal` attributes, never surfaced in HTTP responses (T47.9).
- Test metrics: 2272 passed, 1 skipped — coverage 97.40%.

## Phase 46 — mTLS Inter-Container Communication
*2026-03-22 | PR [#173](../../pull/173)*

- Internal ECDSA P-256 CA and leaf certificates for app, postgres, pgbouncer, and redis;
  idempotent generation script with air-gap compatibility (T46.1).
- mTLS wired on all container-to-container connections: `sslmode=verify-full` for psycopg2/asyncpg,
  `rediss://` URL promotion, TLS params for singleton Redis client (T46.2, ADR-0045).
- Certificate rotation script with backup, chain validation, and expiry check; Prometheus
  `conclave_cert_expiry_days` gauge wired at startup (T46.3).
- K8s NetworkPolicy manifests: default-deny baseline with per-service allow rules for
  app, pgbouncer, postgres, redis, and monitoring (T46.4).

## Phase 45 — Webhook Callbacks, Idempotency Middleware & Orphan Task Reaper
*2026-03-22 | PR [#170](../../pull/170)*

- `IdempotencyMiddleware` reintroduced (TBD-07) using Redis `SET NX EX` with per-operator key
  scoping and graceful Redis degradation (T45.1, ADR-0044).
- `OrphanTaskReaper` wired as a Huey periodic task: detects and marks jobs stuck in
  `PENDING`/`RUNNING` beyond their TTL (T45.2).
- Webhook callbacks with SSRF protection: `shared/ssrf.py` blocks RFC 1918 / loopback / IPv4-mapped
  IPv6 addresses; `set_webhook_delivery_fn` IoC hook wired in bootstrapper (T45.3).
- IPv4-mapped IPv6 SSRF bypass (`::ffff:10.0.0.1`) patched; callback URLs stripped of
  query params before logging (token leakage prevention).

## Phase 44 — Comprehensive Documentation Audit & Cleanup
*2026-03-21 | PR [#169](../../pull/169)*

- Audited 70+ documents across root docs, all ADRs, operational docs, archive, backlog,
  and agent prompts (T44.1–T44.4).
- Fixed 2 BLOCKERs (stale backlog completion markers, document index gaps) and 6 advisory
  items inline; vault re-seal endpoint documentation corrected.
- Produced `DOCUMENT_INDEX.md`: 149-file document registry with lifecycle statuses (T44.5).
- All ADR statuses reconciled; stale "Deferred" labels updated to reflect delivered state.

## Advisory Drain — Pre-Phase 44
*2026-03-21 | PR [#168](../../pull/168)*

- Drained all 8 open advisories (ADV-017 through ADV-024) blocking Phase 44 entry.
- Added JWT auth guards to settings, security, and privacy routers; wired `cleanup_expired_jobs`
  and `cleanup_expired_artifacts` to Huey `@periodic_task` cron jobs at 02:00 UTC and 03:00 UTC.
- Changed `shred_job` audit actor from `"system/api"` to `current_operator` (JWT sub).
- Corrected stale `EpsilonAccountant` references in README and test docstring.

## Phase 43 — Architectural Polish, Code Hygiene & Rule Sunset
*2026-03-21 | T43.1–T43.5 | PRs [#164](../../pull/164)–[#167](../../pull/167)*

- Extracted `_handle_dp_accounting()` and `DpAccountingStep` from `job_orchestration.py`
  into `dp_accounting.py`; `job_orchestration.py` reduced by ~180 lines (T43.1).
- Consolidated 5 repeated optional import patterns (`sdv`, `torch`, `opacus`, `ctgan`, `pandas`)
  into a single `_optional_deps.py` module (T43.2).
- Added `docs/REQUEST_FLOW.md` documenting the full HTTP request lifecycle and conditional
  import pattern (T43.3).
- Batched 4 cosmetic hygiene items per Rule 16 (T43.4).
- Evaluated and deleted 3 Phase-40-sunset rules (Rules 4, 5, 10) from CLAUDE.md; 7 rules
  extended to Phase 50 (T43.5).

## Phase 42 — Security Hardening: Artifact Signing, HTTPS, DP Benchmarks & CORS Docs
*2026-03-21 | T42.1–T42.4 | PRs [#156](../../pull/156)–[#162](../../pull/162)*

- Multi-key artifact signing with versioned signature format (`KEY_ID || HMAC-SHA256`);
  auto-detection of legacy vs. versioned signatures; `build_key_map_from_settings()` moved
  to `shared/security/hmac_signing.py` (T42.1, ADR-0042).
- HTTPS enforcement middleware (`HTTPSEnforcementMiddleware`) checking `X-Forwarded-Proto`,
  rejecting HTTP with 421 in production; `warn_if_ssl_misconfigured()` startup hook (T42.2, ADR-0043).
- DP quality benchmarks executed and documented in `docs/DP_QUALITY_REPORT.md` with actual
  epsilon values, honest analysis of calibration mismatch, and use-case recommendations (T42.3).
- Created `docs/SECURITY_HARDENING.md` covering CORS policy, DDoS mitigation, TLS configuration,
  vault passphrase management, and key rotation procedures (T42.4).

## Phase 41 — Data Retention & Compliance
*2026-03-21 | T41.1–T41.3 | PRs [#153](../../pull/153)–[#155](../../pull/155)*

- Implemented configurable data retention TTLs (`JOB_RETENTION_DAYS`, `AUDIT_RETENTION_DAYS`,
  `ARTIFACT_RETENTION_DAYS`) with Huey scheduled cleanup tasks; legal hold flag prevents
  purge regardless of TTL; manual purge endpoint (`POST /admin/retention/purge`) (T41.1, ADR-0041).
- GDPR Article 17 / CCPA right-to-erasure endpoint (`DELETE /compliance/erasure`) with cascade
  deletion, compliance receipt, and `min_length=1` guard against bulk deletion (T41.2).
- Created `docs/DATA_COMPLIANCE.md` (full compliance policy, GDPR/CCPA/HIPAA guidance,
  erasure procedure, audit trail guarantees) (T41.3).

## Phase 40 — Test Suite Hardening
*2026-03-21 | T40.1–T40.3 | PRs [#150](../../pull/150)–[#152](../../pull/152)*

- Replaced shallow/tautological assertions with value-checking tests across the synthesizer
  module; eliminated rubber-stamp `pytest.raises` patterns (T40.1).
- Rewrote mock-heavy synthesizer tests with behavioral tests; tightened test predicates to
  prevent false positives (T40.2).
- Added missing concurrency, boundary, and performance test categories; fixed `_logger.exception()`
  PII exposure risk in masking worker threads (T40.3).

## Advisory Drain — Pre-Phase 40
*2026-03-21 | branch: fix/advisory-drain-pre-p40*

- Drained 5 open advisories: extracted `EXEMPT_PATHS` to `_exempt_paths.py`, amended ADR-0021
  and ADR-0040, fixed raw key logging in rate limit fallback, amended ADR-0006.

## Phase 39 — Authentication, Authorization & Connection Encryption
*2026-03-20 | T39.1–T39.4 | PRs [#143](../../pull/143)–[#148](../../pull/148)*

- JWT bearer authentication via `get_current_operator()` dependency; `Depends()` added to all
  non-exempt routes; ADR-0039 (T39.1).
- IDOR protection with `owner_id` ownership scoping on all job, connection, and setting queries;
  Alembic migration 008 adding indexed `owner_id` columns; ADR-0040 (T39.2).
- Rate limiting middleware (`RateLimitGateMiddleware`) using Redis sorted sets with fallback to
  in-process deque; configurable per-operator limits (T39.3).
- ALE encryption of connection metadata (`host`, `port`, `database`, `username`, `password`)
  with Alembic migration 007 for key rotation support (T39.4).

## Advisory Drain — Pre-Phase 39
*2026-03-20 | branch: fix/advisory-drain-pre-p39*

- Drained 5 advisories from Phase 38 and E2E load test: audit write failure handling,
  stale E2E doc assertions, non-fatal exception handling, dev-only DSN caveat,
  `calculate_rows_per_sec` negative duration guard.

## E2E 1M-Row Load Test
*2026-03-20 | branch: test/e2e-1m-row-load-test*

- Validated full pipeline at production scale: 1,011,540 source rows across 4 tables;
  4 CTGAN synthesis jobs COMPLETE with correct DP accounting (ε up to 9.89); all artifacts shredded.
- Fixed 5 review findings across QA and DevOps; overwritten `docs/E2E_VALIDATION.md` with evidence.

## Phase 38 — Audit Integrity, Timing Side-Channel Fix & Pre-Commit Hardening
*2026-03-19 | T38.1–T38.4*

- If `AuditLogger.log_event()` raises during a job's DP accounting step, the job is marked
  FAILED — privacy budget spend MUST have an audit entry (T38.1, Constitution Priority 0).
- Vault timing side-channel eliminated: `derive_kek()` runs unconditionally before the
  empty-passphrase check, preventing oracle attacks (T38.2, ADR-0009 amended).
- Import-linter enforcement confirmed already in pre-commit since Phase 20 — T38.3 task
  verified satisfied, no changes required.
- Batched 4 documentation and hygiene items (T38.4).

## Phase 37 — Advisory Drain, CHANGELOG Currency & E2E Demo Capstone
*2026-03-19 | T37.1–T37.3*

- Fixed silent privacy budget deduction failure: if `epsilon_spent()` raises, job is marked
  FAILED with `EpsilonMeasurementError` — prevents untracked DP use (T37.1).
- Drained 4 advisory items: `safe_error_msg()` wrapping in error logs, stale PIIFilter
  reference removal, `config_validation.py` delegating to `get_settings()` singleton (T37.2).
- Added `EpsilonMeasurementError` to shared exception hierarchy, `OPERATOR_ERROR_MAP`,
  ADR-0037, and ADR-0038.
- CHANGELOG backfilled through Phase 36 (T37.3).

## Phase 36 — Configuration Centralization, Documentation Pruning & Hygiene
*2026-03-19 | T36.1–T36.4*

- Consolidated 14 environment variables into a typed, validated `ConclaveSettings` Pydantic
  model with `@lru_cache` singleton; eliminated scattered `os.environ` reads across the codebase.
- Decomposed the 449-line `errors.py` into a 4-file package (max 197 lines per file) with all
  import paths preserved via re-exports and `CycleDetectionError`/`CollisionError` relocated to
  `shared/exceptions.py` per ADR-0037.
- Pruned `BUSINESS_REQUIREMENTS.md` from 257 to 42 lines; aligned DP claims with implementation
  reality; archived stale documentation to `docs/retired/`.
- Added 22 edge-case tests closing audit gaps in masking salt, HMAC verification, vault, and
  privacy budget precision; 1,561 unit tests at 97.93% coverage.

## Phase 35 — Synthesis Layer Refactor & Test Replacement
*2026-03-18 | T35.1–T35.4*

- Decomposed `_run_synthesis_job_impl()` from 232 lines into a 47-line step pipeline with a
  `SynthesisJobStep` Protocol; each step is independently testable and status transitions are
  owned exclusively by the orchestrator (ADR-0038).
- Reduced `dp_training.py` from 1,144 to 497 lines (57%) via strategy pattern — discriminator
  training, proxy-model fallback, and DP accounting each extracted to focused strategy classes.
- Replaced tautological tests (54:1 and 79:1 setup-to-assertion ratios) with behavioral and
  contract tests; wired `OomCheckStep` into the step pipeline, removing the legacy bypass path.
- Added full E2E pipeline integration test: 5-table FK chain, 105 rows, real PostgreSQL,
  zero mocks below the API boundary; 1,514 unit tests at 98.04% coverage.

## Phase 34 — Exception Hierarchy Unification & Operator Error Coverage
*2026-03-18 | T34.1–T34.3*

- Unified all 11 domain exceptions under `SynthEngineError`; middleware now catches every
  domain error and returns structured RFC 7807 responses with no internal details leaked.
- Completed `OPERATOR_ERROR_MAP` with RFC 7807 mappings for all exception types, including
  security-event exceptions (`PrivilegeEscalationError`, `ArtifactTamperingError`) verified
  by HTTP round-trip leak tests.
- Reconciled `VaultAlreadyUnsealedError` status code conflict (400 vs. 409) across lifecycle
  handler and error map; consolidated vault exception imports to canonical `shared/exceptions`.
- Documented the exception hierarchy architecture in ADR-0037; 38 new tests added.

## Phase 33 — Governance Hygiene, Documentation Currency & Codebase Polish
*2026-03-18 | T33.1–T33.4*

- Evaluated CLAUDE.md rule sunset: Rule 13 deleted (never prevented a failure); Rule 11
  advisory threshold tightened; 8 rules renewed with Phase 40 sunset; CLAUDE.md reduced from
  267 to 259 lines.
- Added `pydoclint` as a mandatory pre-commit gate, closing the recurring docstring-drift gap
  that caused findings in Phases 30, 31, and 32; scoped to `src/synth_engine/` with
  `arg-type-hints-in-docstring = false`.
- Backfilled `CHANGELOG.md` and 8 phase summaries (Phases 21-28); added static API reference;
  amended ADR-0002 to reflect superseded status; pinned documentation metrics.
- Tightened 6 dependency version ranges to current minor versions; removed stale `TODO` markers.

## Phase 32 — Dead Module Cleanup & Development Process Documentation
*2026-03-18 | PR [#127](../../pull/127)*

- Removed three dead scaffolding modules (Redis idempotency middleware, orphan task reaper,
  zero-trust JWT binding) that were ADR'd and partially implemented but never wired; deferred
  as TBD-06/TBD-07/TBD-08 in `docs/deferred-items.md`.
- Added `docs/DEVELOPMENT_STORY.md`: full case study of the project's governance-driven AI
  development methodology, timeline, what went wrong, and what went right.
- Updated README with "How This Was Built" section and development process overview.

## Phase 31 — Code Health & Bus Factor Elimination
*2026-03-18 | PR [#126](../../pull/126)*

- Decomposed `dp_training.py` from 218 lines to 75 lines by extracting three focused helpers.
- Audited vulture whitelist — removed stale entries; all remaining entries justified.
- Added missing docstrings across synthesizer module; corrected docstring-variable drift
  (`steps_per_epoch` reference after inline refactor).

## Phase 30 — Discriminator-Level DP-SGD
*2026-03-18 | PR [#124](../../pull/124), PR [#125](../../pull/125)*

- Replaced the Phase 7 proxy-model DP compromise with discriminator-level DP-SGD using
  Opacus `make_private_with_epsilon()` directly on `OpacusCompatibleDiscriminator`.
- DP accounting now reflects actual Discriminator gradient steps on real training data —
  the standard DP-GAN threat model.
- Proxy-model fallback (`_activate_opacus_proxy`) retained for environments where Opacus
  cannot instrument the Discriminator.
- Added ADR-0036 documenting the discriminator-level DP-SGD architecture.

## Phase 29 — Documentation Integrity & Review Debt
*2026-03-18 | PR [#123](../../pull/123)*

- Raised test coverage floor from 90% to 95% (constitutional amendment).
- Fixed stale docstrings claiming "WGAN-GP" when the loop uses plain WGAN (Opacus
  incompatibility with `torch.autograd.grad` documented).
- Repaired six documentation accuracy issues identified in Phase 28 review.

## Phase 28 — Full E2E Validation
*2026-03-18 | PR [#122](../../pull/122)*

- Ran full end-to-end validation with Playwright browser screenshots and a 11,000-row
  synthesis load test across 4 tables.
- Found and fixed 5 production bugs: Docker multi-stage build skipping packages, wrong
  Tini path, missing synthesizer deps in Docker image, `asyncio.run()` in Huey worker
  thread, `np.float64` psycopg2 serialization failure.
- Privacy budget tracking confirmed: 28.33 epsilon spent from 100 allocated.

## Phase 27 — Frontend Production Hardening
*2026-03-18 | PR [#121](../../pull/121)*

- Added responsive breakpoints for mobile and tablet viewpoints.
- Standardized `AsyncButton` component across all interactive actions.
- Added Playwright E2E accessibility tests covering WCAG 2.1 AA requirements.

## Phase 26 — Backend Production Hardening
*2026-03-18 | PR [#120](../../pull/120)*

- Split large router files into focused sub-modules (job orchestration, job lifecycle).
- Established a shared exception hierarchy to resolve the cross-module exception import
  problem (ADR-0033 follow-up, ADR-0034).
- Added Protocol typing for all DI callback boundaries.
- Added HTTP round-trip tests for all production error paths.
- Fixed squash-merge Constitutional violation: PRs now merge with `--merge` to preserve
  the TDD commit trail.

## Phase 25 — Observability
*2026-03-17 | PR [#119](../../pull/119)*

- Added custom Prometheus metrics: job queue depth, synthesis duration histogram,
  epsilon spent gauge, privacy budget remaining gauge.
- Wired OTEL trace propagation from HTTP routes through Huey worker background tasks.

## Phase 24 — Integration Test Repair
*2026-03-17 | PR [#118](../../pull/118)*

- Fixed parameter rename regression (`n_rows` → `num_rows`) caught only by integration
  tests against real SDV — unit mocks did not enforce the keyword argument signature.
- Fixed CLI wiring for synthesis pipeline (parameter was not passed through).
- Added test isolation guards preventing shared database state between integration tests.

## Phase 23 — Job Lifecycle Completion
*2026-03-17 | PRs [#113](../../pull/113)–[#117](../../pull/117)*

- Added generation step to Huey synthesis task — jobs now produce real Parquet artifacts.
- Added `/jobs/{id}/download` streaming endpoint with HMAC artifact verification.
- Added cryptographic erasure endpoint (`/jobs/{id}/shred`) implementing NIST SP 800-88.
- Added frontend Download button for COMPLETE jobs.
- Security fixes: Content-Disposition header injection, path traversal in `extractFilename`.

## Phase 22 — DP Pipeline End-to-End Integration
*2026-03-17 | PRs [#106](../../pull/106)–[#112](../../pull/112)*

- Added DP parameters (epsilon, delta, max_grad_norm, num_epochs) to `SynthesisJob` and
  the job creation API.
- Wired `DPTrainingWrapper` into the synthesis pipeline (Huey task → DP engine → CTGAN).
- Wired `spend_budget()` into the synthesis pipeline with budget exhaustion blocking.
- Added Budget Management API endpoints (`GET /privacy/budget`, `POST /privacy/budget/refresh`).
- Fixed DI factory injection to replace `importlib.import_module` boundary circumvention.
- Added full DP synthesis pipeline E2E integration test.

## Phase 21 — CLI Masking Config Fix & E2E Smoke Tests
*2026-03-16 | PRs [#103](../../pull/103)–[#105](../../pull/105)*

- Fixed `mask_name` split: per-column masking functions now correctly applied
  (`split_mask_name` → `first_name`/`last_name`).
- Added automated E2E smoke test for the CLI subset+mask pipeline.
- Documented full E2E validation evidence and UI screenshots in README.

## Phase 20 — Architecture Tightening
*2026-03-16 | PRs [#98](../../pull/98)–[#102](../../pull/102)*

- Wired `import-linter` contracts into pre-commit hooks (ADR-0032); 4 contracts, 0 violations.
- Fixed FK traversal OOM edge case (ADV-020).
- Expanded integration tests against real Docker Compose services.
- Fixed Docker Compose service configuration issues blocking local integration runs.
- Added WCAG accessibility improvements: `aria-required`, `aria-invalid`.

## Phase 19 — Live E2E Pipeline Validation (First Attempt)
*2026-03-16 | PRs [#93](../../pull/93)–[#97](../../pull/97)*

- Discovered FK traversal bug ADV-021: subsetting engine had never traversed FKs via the
  CLI path (19 phases invisible to unit tests; fixed in Phase 20).
- Added property-based tests (Hypothesis) for privacy budget arithmetic.
- Enforced `MASKING_SALT` at runtime; added pgbouncer `scram-sha-256` documentation.
- Refactored middleware to pure ASGI; added engine singleton.

## Phase 18 — E2E Validation Infrastructure
*2026-03-16 | PRs [#90](../../pull/90)–[#92](../../pull/92)*

- Added sample data fixtures for E2E validation runs (fictional, no PII).
- Moved `chromadb` to dev-only optional dependency group (ADV-015).
- Conducted `# type: ignore` suppression audit — reduced count and added inline justifications.

## Phase 17 — Process Governance & Security
*2026-03-16 | PRs [#86](../../pull/86)–[#89](../../pull/89)*

- SHA-pinned all Docker base images to SHA-256 digests (ADV-014).
- Added WCAG `aria-required`/`aria-invalid` to dashboard forms.
- Cleaned stale CLAUDE.md references and phase-16 spec inconsistency.
- Slimmed process governance rules (retired redundant rules).

## Phase 16 — Correctness Sprint
*2026-03-16 | PRs [#81](../../pull/81)–[#85](../../pull/85)*

- Fixed Alembic migration 003: epsilon column precision (`FLOAT8` → `NUMERIC`).
- Fixed frontend supply chain: npm audit flags addressed; `.env.example` added.
- Added WCAG skip navigation links.
- Fixed `nosec` annotation accuracy audit.

## Phases 10–15 — Quality Infrastructure
*2026-03-16 | PRs [#67](../../pull/67)–[#81](../../pull/81)*

- Phase 10: Drained stale `TODO(T4.4)`; fixed `pytest-asyncio` Python 3.14 compatibility.
- Phase 11: Workspace hygiene (worktrees, spikes, `.gitignore`); ADR-0029 architecture
  gap analysis; documentation currency pass.
- Phase 12: Vulture whitelist for FastAPI/Pydantic false positives.
- Phase 13: Fixed vulture whitelist ruff compliance.
- Phase 14: Fixed 8 integration test failures (DP, privacy, SSE); ESLint 9.x config.
- Phase 15: Repaired frontend test coverage gate (85.66% → 97.35%).

## Phase 9 — Bootstrapper Decomposition
*2026-03-16 | PRs [#64](../../pull/64)–[#66](../../pull/66)*

- Decomposed `main.py` from 533 to 183 lines by extracting router registry, lifecycle
  hooks, factory helpers, and middleware.
- Drained all 5 open advisories.
- Added startup config validation (`validate_config()`) — process exits immediately on
  missing required environment variables.
- Refreshed Operator Manual for Phase 6–9 changes.

## Phase 8 — Advisory Drain Sprint
*2026-03-16 | PRs [#59](../../pull/59)–[#63](../../pull/63)*

- Dedicated advisory drain phase: cleared all advisories accumulated in Phases 4–7.
- Key drains: HMAC artifact signing (ADV-050/054), integration test gaps (ADV-021/064),
  data model cleanup (ADV-071), CI infrastructure fixes (ADV-052/062/065/066/069/070/072).
- `esbuild` CVE resolved; Opacus ADR created (ADR-0026).

## Phase 7 — DP-SGD Integration
*2026-03-16 | PRs [#54](../../pull/54)–[#58](../../pull/58)*

- ADR-0025: Custom CTGAN training loop architecture.
- Implemented `DPCompatibleCTGAN` with Opacus DP-SGD integration seam.
- Added Opacus `PrivacyEngine` end-to-end wiring.
- Added `ProfileDelta` validation and DP quality benchmarks.
- Added E2E DP synthesis integration tests.

## Phase 6 — Security Hardening & E2E Tests
*2026-03-15 | PRs [#50](../../pull/50)–[#52](../../pull/52)*

- E2E generative synthesis subsystem tests.
- NIST SP 800-88 erasure validation.
- OWASP ZAP baseline scan in CI.
- JSON/NaN fuzz testing for API inputs.
- Final security remediation; platform handover documentation.

## Phase 5 — Frontend & License Activation
*2026-03-15 | PRs [#42](../../pull/42)–[#49](../../pull/49)*

- Offline license activation protocol (RS256 JWT with hardware binding; no call-home).
- Cryptographic shredding and re-keying API.
- Accessible React SPA: Vault Unseal flow, keyboard nav, semantic headings.
- Data Synthesis Dashboard with SSE real-time progress and `localStorage` rehydration.

## Phase 4 — Synthesizer & Privacy Accountant
*2026-03-15 | PRs [#27](../../pull/27)–[#41](../../pull/41)*

- CTGAN/SDV synthesizer integration with Huey task wiring and checkpointing.
- OOM pre-flight guardrail: jobs blocked before training if memory would be exceeded.
- `StatisticalProfiler` and `ProfileDelta` (modules/profiler).
- GPU passthrough and ephemeral MinIO storage for synthesizer artifacts.
- Opacus `DPTrainingWrapper` integration.
- `EpsilonAccountant`: global epsilon/delta ledger with per-table budget enforcement.

## Phase 3 / Phase 3.5 — Ingestion, Mapping, Masking, Subsetting
*2026-03-14 | PRs [#15](../../pull/15)–[#26](../../pull/26)*

- PostgreSQL ingestion adapter: streaming, privilege pre-flight check, schema inspector.
- Relational mapping module: DAG construction, Kahn topological sort, cycle detection.
- Deterministic masking engine: Feistel FPE, LUHN algorithm, collision prevention.
- Subsetting core: FK-aware relational traversal, Saga egress pattern, EgressWriter.
- `SchemaTopology` immutability (`MappingProxyType`) and virtual FK support.
- `conclave-subset` CLI with bootstrapper wiring and `CycleDetectionError` 422 handling.
- SHA-pinned GitHub Actions; Trivy container scan; PostgreSQL 16 pin.

## Phase 2 — Application Foundation
*2026-03-13 | PRs [#7](../../pull/7)–[#14](../../pull/14)*

- FastAPI bootstrapper: OTEL, async idempotency middleware, orphan task reaper.
- PostgreSQL + PgBouncer + SQLModel ORM with Application-Level Encryption (ALE).
- Zero-trust JWT authentication with client binding and RBAC scopes.
- Vault unseal pattern: operator passphrase derives KEK at runtime; never persisted.
- WORM audit logger: cryptographically signed, append-only audit trail.
- Prometheus/Grafana observability stack.

## Phase 1 — CI/CD & Project Foundation
*2026-03-13 | PRs [#1](../../pull/1)–[#6](../../pull/6)*

- Autonomous Agile environment provisioning (scripts, CI pipeline).
- Poetry project, quality gates: `ruff`, `mypy`, `bandit`, `gitleaks`, `pytest`.
- TDD framework established; first passing test suite.
- Docker infrastructure: base image, security hardening, dev-experience tooling,
  air-gap bundle builder (`make build-airgap-bundle`).

## Phase 0.8 — Technical Spikes
*2026-03-13 | PRs [#3](../../pull/3)–[#5](../../pull/5)*

- ML memory physics spike: chunked synthesizer within 2GB memory ceiling.
- FPE-LUHN spike: zero-collision deterministic credit card masking.
- Topological subset spike: FK-inferred CTE generation with streaming extractor.

---

*This changelog covers Phase 0.8 through Phase 50 (as of 2026-03-23).*
*For the most current state, refer to `git log` and the merged PR list.*
