# Conclave Engine — Retrospective Log

Living ledger of review retrospective notes and open advisory items.
Updated after each task's review phase completes.

---

## Table of Contents

### Open Advisories by Severity

**SECURITY (TTL enforced — Rule 26)**

| ID | Advisory | Phase |
|----|----------|-------|
| (none) | All security advisories resolved as of P69. | — |

**ADVISORY**

| ID | Advisory | Phase |
|----|----------|-------|
| ADV-P80-01 | DB-error-path tests in test_rbac_feature.py have setup-to-assertion ratios approaching 7:1-8:1. Justified by multi-patch context management for SQLAlchemy error simulation. Track for refactor opportunity. | P80 |
| ADV-P80-02 | Shared passphrase model (`verify_operator_credentials`) is structurally incompatible with multi-tenant role isolation. Per-user credentials or SSO-only auth needed for true multi-tenant RBAC. Architectural — future phase. | P80 |
| ADV-P80-03 | Rate limiter uses per-process `MemoryStorage` during Redis failover. In N-worker K8s deployments, effective rate limit multiplies by pod count. Pre-existing (carried from P75). | P80 |

### Deferred by Tier

Items logged here belong to maturity tiers above the current tier. They do not count toward
the Rule 11 advisory cap and do not block merges. They are promoted to active advisories when
the system enters their target tier.

| ID | Target Tier | Summary | Raised Phase |
|----|-------------|---------|--------------|
| _(No deferred items — all 7 tiers assessed COMPLETE as of 2026-04-01. All open advisories are at-tier.)_ | | | |

### [2026-04-09] Phase 80 — Role-Based Access Control (RBAC)

**Tasks**: T80.0 (ADR-0066), T80.1 (Role model, permission matrix), T80.2 (Permission middleware on all endpoints), T80.3 (Admin user management endpoints), T80.4 (Auditor role, audit log endpoint), T80.5 (Erasure semantics update)

**Summary**: Replaces single "operator" role with 4-role hierarchy (admin, operator, viewer, auditor) and 20-permission matrix. `require_permission()` FastAPI dependency factory enforces permissions on all endpoints, replacing `get_current_operator`. Admin user management CRUD endpoints with last-admin guard (SELECT FOR UPDATE). Audit log endpoint with auditor-access logging. Admin-delegated erasure. DB role resolution in auth/token for multi-tenant mode. Alembic migration 010 for UniqueConstraint(org_id, email). ADR-0066 supersedes ADR-0049. MappingProxyType for immutable permission matrix. Prometheus counter for role resolution failures.

**Spec-challenger findings**: 12 missing ACs, 37 negative tests, 7 attack vectors — all incorporated into developer brief.

**Review findings fixed**: 6 BLOCKERs across 2 fix rounds: B1 (auth/token hardcoded role="admin"), B2 (last-admin TOCTOU race — no FOR UPDATE), B3 (erasure IDOR guard dead code), B4 (zero integration tests), B5 (auth.py imports non-existent `_engine` — silent privilege escalation via `# type: ignore`), B6 (missing Alembic migration for UniqueConstraint). 22 FINDINGs: stale JWT doc, email uniqueness, jobs:cancel orphan, admin/users pagination, audit log org-scoping, MappingProxyType, settings docstring, cryptography CVE, tautological assertions, fixture duplication, gate-exempt placement, docstring accuracy, DB error path tests, unreachable handler, role query org_id filter, Prometheus counter, erasure IDOR no-op removal, exception narrowing, admin fallback tests, list_users total docstring, duplicate assertions, schema assertion specificity.

**Production-to-test LOC ratio**: 1:2.65. Marginally exceeds 1:2.5 threshold. Justified: RBAC requires per-permission, per-role endpoint isolation tests (20 permissions × 4 roles = 80 combinations). Consistent with P79 precedent for security-critical enforcement code.

**E2E**: Playwright 36 passed, 0 failed.

**Boundary audit**: PASS. 1 FINDING resolved (schema assertion specificity). 2 ADVISORYs logged (ADV-P80-01, ADV-P80-02). 1 merged branch cleaned up.

**Advisory count**: 3 open (ADV-P80-01, ADV-P80-02, ADV-P80-03). Below Rule 11 threshold of 8.

**CLOSED (P80 — RBAC)**

| ID | Resolution | Closed |
|----|-----------|--------|
| ADV-P79-01 | CLOSED — Test setup duplication resolved in refactor commit `de10341`. Shared IDOR test fixture extracted. | P80 |
| ADV-P79-02 | CLOSED — ADR-0049 superseded by ADR-0066. Stale section 4 is moot. Status header updated. | P80 |

### [2026-04-04] Phase 79 — Multi-Tenancy Foundation

**Tasks**: T79.0b (shared/models subpackage), T79.0 (ADR-0065 JWT identity), T79.1 (Organization/User models, migration 009), T79.2 (tenant-scoped queries, TenantContext), T79.3 (tenant isolation tests), T79.4 (per-tenant privacy ledger)

**Summary**: Transforms single-operator model into multi-tenant with org-level data isolation. New `TenantContext` frozen dataclass replaces `get_current_operator` across all routers. `org_id` FK added to Connection, SynthesisJob, WebhookRegistration, PrivacyLedger, PrivacyTransaction. JWT Option C (short-lived embed, ≤900s expiry). Per-org connection semaphore. Alembic migration 009 with idempotent default org/user seeding. ADR-0065 supersedes ADR-0040 and ADR-0062. Assumption A-014 registered (application-level tenant isolation).

**Spec-challenger findings**: 12 missing ACs, 27 negative tests, 7 attack vectors, 4 config risks — all incorporated into developer brief.

**Review findings fixed**: 5 BLOCKERs (admin.py IDOR, webhook dispatch cross-org, missing migration, missing integration tests, Huey org_id validation) and 18 FINDINGs (erasure org_id scoping, create_token org_id/role, UUID validation, role allowlist, Prometheus org_id label, .env.example, settings router migration, ADR status updates, dead code, semaphore race fix, pass-through opt-in, docstring accuracy, rubber-stamp assertions, cross-org budget guard) across 2 fix rounds.

**Production-to-test LOC ratio**: 1:3.35. Exceeds 1:2.5 threshold. Justified: security-critical IDOR boundary enforcement requires per-endpoint isolation tests that cannot be parametrized without sacrificing fault localization. The `fix:` commit updating ~30 pre-existing test files to `TenantContext` is a one-time migration cost, not ongoing test verbosity.

**Gate #2**: 3709 passed, 7 skipped, 96.20% coverage. All static analysis green.

**E2E**: Playwright 36 passed, 0 failed.

**Boundary audit**: PASS. 2 ADVISORYs noted (ADV-P79-01 test setup duplication, ADV-P79-02 ADR-0049 stale section). 8 merged branches cleaned up.

**Advisory count**: 2 open (ADV-P79-01, ADV-P79-02). Below Rule 11 threshold of 8.

**CLOSED (P76 — advisory drain & polish)**

| ID | Resolution | Closed |
|----|-----------|--------|
| ADV-P70-04 | CLOSED — Composite FK traversal covered by unit tests (test_subsetting_composite_fk_attack.py). Integration test deferred: requires PostgreSQL fixtures with composite FK schema not available in CI. | P76 |
| ADV-P73-01 | CLOSED — Accepted tradeoff. Ratio driven by enforcement gates and fault injection infrastructure. Waived per spec-challenger recommendation (P73). | P76 |
| ADV-P73-02 | CLOSED — Accepted incremental adoption tradeoff. E712 rule intentionally disabled in tests/ to support explicit `== True` assertion pattern. | P76 |

**CLOSED (P75 — multi-worker safety & observability)**

| ID | Resolution | Closed |
|----|-----------|--------|
| ADV-P62-03 | CLOSED — Redis-backed `RedisCircuitBreaker` with `conclave:cb:` key prefix, TTL=cooldown_seconds, SET NX EX half-open probe coordination. Falls back to process-local on Redis unavailability. (T75.1) | P75 |
| ADV-P63-01 | CLOSED — Grace period start stored in Redis `conclave:grace:started` using `time.time()` (UTC epoch). TTL=grace_period*2. Key deleted on recovery. Falls back to process-local. (T75.2) | P75 |
| ADV-P71-01 | CLOSED — `validate_prometheus_multiproc_dir()` fail-closed validation at startup. Dir must be absolute, exist, be writable, not inside source tree. `.env.example` documented. (T75.3) | P75 |

**CLOSED (P74 — maintainability & configuration hardening)**

| ID | Resolution | Closed |
|----|-----------|--------|
| ADV-P70-01 | CLOSED — `settings.py` decomposed to ≤300 LOC via T74.3. Validators and field groups moved to `settings_models.py`. | P74 |

**CLOSED (P71 — audit coverage completion & polish)**

| ID | Resolution | Closed |
|----|-----------|--------|
| ADV-P70-02 | CLOSED — `conclave-audit` CLI group wired with `migrate-signatures` and `log-event` subcommands. Entry point registered in pyproject.toml. Runbook commands now functional. (T71.2) | P71 |
| ADV-P70-03 | CLOSED — Unified `AUDIT_WRITE_FAILURE_TOTAL` counter in `shared/observability.py` with `router` + `endpoint` labels. 4 fragmented per-router counters removed. (T71.5) | P71 |

**CLOSED (P70 — structural debt reduction)**

| ID | Resolution | Closed |
|----|-----------|--------|
| ADV-P68-03 | CLOSED — `max_length=255` added to `connection_id` and `webhook_id` path params across all endpoints (T70.7) | P70 |
| ADV-P68-04 | CLOSED — Audit-before-mutation standardized in `jobs.py:shred_job` and `privacy.py:refresh_budget` with compensating events on post-audit failure (T70.8) | P70 |
| ADV-P68-05 | CLOSED — `AUDIT_WRITE_FAILURE_TOTAL` counters added to all audit-fail catch blocks in admin.py, security.py, jobs.py, privacy.py (T70.9) | P70 |

**CLOSED (P69 — security depth & test coverage)**

| ID | Resolution | Closed |
|----|-----------|--------|
| ADV-P68-01 | CLOSED — `DELETE /compliance/erasure` enforces `body.subject_id == current_operator` (403 on mismatch). IDOR check fires before vault-sealed check. Audit event on cross-operator attempt. (T69.6) | P69 |
| ADV-P68-02 | CLOSED — `conclave_data_dir` setting with `model_validator`. `validate_parquet_path` enforces `Path.resolve() + is_relative_to()` sandbox. Root `/` forbidden. Production requires absolute path. (T69.7) | P69 |

**CLOSED (P68 — critical safety hardening)**

| ID | Resolution | Closed |
|----|-----------|--------|
| ADV-P67-01 | PARTIALLY CLOSED — `max_length` added to `table_name`(255), `parquet_path`(1024), `callback_url`(2048), `signing_key`(512), `justification`(2000) in T68.6. Remaining: `connection_id`, `webhook_id` path params — tracked as ADV-P68-03. | P68 |
| ADV-P67-02 | CLOSED — `validate_config()` now raises `SystemExit` when `rate_limit_fail_open=True` in production mode (T68.7) | P68 |

**CLOSED (P67 — input validation & error mapping hardening)**

| ID | Resolution | Closed |
|----|-----------|--------|
| ADV-P66-01 | CLOSED — `_SettingKey = Annotated[str, Path(max_length=255)]` applied to all 3 settings endpoints (T67.1) | P67 |
| ADV-P66-02 | CLOSED — `TokenRequest.passphrase` gains `max_length=1024` matching `UnsealRequest` (T67.2) | P67 |
| ADV-P66-03 | CLOSED — `TLSCertificateError` added to `OPERATOR_ERROR_MAP` with status 400, static detail string (T67.3) | P67 |

**CLOSED (P66 — expired security advisory resolution)**

| ID | Resolution | Closed |
|----|-----------|--------|
| ADV-P62-01 | CLOSED — OpenAPI docs (/docs, /redoc, /openapi.json) disabled in production mode via `docs_url=None`, `redoc_url=None`, `openapi_url=None` (T66.2) | P66 |
| ADV-P62-02 | CLOSED — `trusted_proxy_count` setting added (zero-trust default); XFF ignored unless explicitly configured; IP format validation via `ipaddress.ip_address()` (T66.3) | P66 |
| ADV-P63-03 | CLOSED — single-operator privacy ledger assumption documented in ADR-0062; code comments added to both ledger queries (T66.6) | P66 |
| ADV-P63-05 | CLOSED — pygments confirmed dev-only transitive dependency, not in production Docker image; documented in ADR-0061 (T66.4) | P66 |

**CLOSED (P65 advisory drain — T65.1)**

| ID | Resolution | Closed |
|----|-----------|--------|
| ADV-P58-01 | CLOSED — dead `_sign_v1/_sign_v2/_sign_v3` wrappers deleted from AuditLogger; tests updated to use standalone sign_v1/sign_v2 functions (T65.1) | P65 |
| ADV-P58-02 | CLOSED — `UNEXPECTED_WEBHOOK_ERRORS_TOTAL` counter added to wiring.py CRITICAL branch (T65.1) | P65 |
| ADV-P59-01 | CLOSED — CI SBOM documents dev-only subset; synthesizer deps not in production image | P65 |
| ADV-P59-02 | CLOSED — asyncio.run() in threadpool is accepted tradeoff per ADR-0059 assessment | P65 |
| ADV-P59-04 | CLOSED — FastAPI default 422 handler covers validation errors; no custom 400 needed | P65 |
| ADV-P61-01 | CLOSED — infrastructure marker exists; CI coverage split deferred to when test-infra phase occurs | P65 |
| ADV-P62-04 | CLOSED — `WEBHOOK_DELIVERIES_SKIPPED_TOTAL` counter added with `reason="circuit_open"` label (T65.1) | P65 |
| ADV-P63-02 | CLOSED — duplicate of ADV-P62-02 | P65 |
| ADV-P63-04 | CLOSED — `RATE_LIMIT_REDIS_FALLBACK_TOTAL` counter pre-initialized with all 4 tier labels (T65.1) | P65 |
| ADV-P64-02 | CLOSED — ADR-0058 amended to document T64.3 rate_limit decomposition pattern (T65.1) | P65 |

---

### Open Advisories by Domain

**Security**
- (none — all resolved in P66)

**Infrastructure**
- (none — ADV-P62-03 and ADV-P63-01 closed in P75)

**Maintainability**
- (none — ADV-P70-01 closed in P74)

**Observability**
- (none — ADV-P71-01 closed in P75)

**Testing**
- (none — ADV-P70-04, ADV-P73-01, ADV-P73-02 closed in P76)

---

### Phase Index

| Phase | Date | Link |
|-------|------|------|
| Phase 76 | 2026-03-29 | [Advisory Drain & Polish](#2026-03-29-phase-76--advisory-drain--polish) |
| Phase 74 | 2026-03-31 | [Maintainability & Configuration Hardening](#2026-03-31-phase-74--maintainability--configuration-hardening) |
| Phase 73 | 2026-03-29 | [Test Quality Rehabilitation](#2026-03-29-phase-73--test-quality-rehabilitation) |
| Phase 72 | 2026-03-29 | [Exception Specificity & Router Safety Hardening](#2026-03-29-phase-72--exception-specificity--router-safety-hardening) |
| Phase 71 | 2026-03-29 | [Audit Coverage Completion & Polish](#2026-03-29-phase-71--audit-coverage-completion--polish) |
| Phase 70 | 2026-03-29 | [Structural Debt Reduction](#2026-03-29-phase-70--structural-debt-reduction) |
| Phase 69 | 2026-03-29 | [Security Depth & Test Coverage](#2026-03-29-phase-69--security-depth--test-coverage) |
| Phase 68 | 2026-03-28 | [Critical Safety Hardening](#2026-03-28-phase-68--critical-safety-hardening) |
| Phase 67 | 2026-03-28 | [Advisory Drain: Input Validation & Error Mapping Hardening](#2026-03-28-phase-67--advisory-drain-input-validation--error-mapping-hardening) |
| Phase 66 | 2026-03-28 | [Expired Security Advisory Resolution](#2026-03-28-phase-66--expired-security-advisory-resolution) |
| Phase 65 | 2026-03-27 | [Advisory Drain & Polish](#2026-03-27-phase-65--advisory-drain--polish) |
| Phase 64 | 2026-03-27 | [Review Summary](#2026-03-27-phase-64--review-summary) |

---

### [2026-03-29] Phase 76 — Advisory Drain & Polish

**Tasks**: T76.1 (set_spend_budget_fn double-set WARNING), T76.2 (OPERATOR_MANUAL PROMETHEUS_MULTIPROC_DIR), T76.3 (docker-compose PROMETHEUS_MULTIPROC_DIR), T76.4 (advisory drain: ADV-P70-04, ADV-P73-01, ADV-P73-02)

**Source**: P75 QA findings + open advisory drain (Rule 11 maintenance).

**Delivered**:
- `set_spend_budget_fn()` double-set WARNING added — matches `set_dp_wrapper_factory()` and `set_webhook_delivery_fn()` pattern. Emits WARNING when callable already registered, consistent with T75.4 wiring safety design.
- Section 10.3 added to OPERATOR_MANUAL.md: `PROMETHEUS_MULTIPROC_DIR` definition, when to set it, all four directory requirements (absolute, exists, writable, not inside source tree), stale `.db` file cleanup procedure before worker restart, example configuration.
- `PROMETHEUS_MULTIPROC_DIR` commented-out entry added to `docker-compose.yml` app service environment section with prerequisites and cleanup instructions.
- Advisory table drained from 3 open → 0 open: ADV-P70-04 closed by documented acceptance (unit test coverage, infrastructure gap acknowledged), ADV-P73-01 closed by documented acceptance (waived per spec-challenger), ADV-P73-02 closed by documented acceptance (intentional E712 disable in tests/).

**Review agents**: Pending independent review.

**New advisories**: None.

**Advisory count**: 0 open. All advisories drained to zero.

---

### [2026-03-31] Phase 74 — Maintainability & Configuration Hardening

**Tasks**: T74.1 (DB pool params to settings), T74.2 (rate limit window to settings), T74.3 (decompose settings.py), T74.4 (break ≥100 LOC functions), T74.5 (break 50-100 LOC functions), T74.6 (documentation cleanup)

**Source**: Production Audit 2026-03-29 findings C5-C8 + ADV-P70-01.

**Delivered**:
- 6 DB pool parameters externalized to ConclaveSettings (env vars with Pydantic validation, gt=0/le=max bounds)
- Rate limit window (CONCLAVE_RATE_LIMIT_WINDOW_SECONDS) externalized to settings; startup warning added when non-default value detected (T74.2 red-team finding)
- settings.py decomposed from ~1025 LOC to 183 LOC; all validators and field groups moved to settings_models.py; get_settings() API unchanged; ADV-P70-01 closed
- All public endpoint and module functions reduced to ≤50 LOC via private helper extraction (_check_job_ownership, _fetch_ledger_or_raise, _check_circuit_breaker, etc.)
- FastAPI decorator placement bug found and fixed during helper extraction (decorators on helpers instead of endpoints caused FastAPIError at import time — systematic fix across 4 router files)
- 66 phase backlog files archived to docs/retro_archive/; RETRO_LOG trimmed to last 10 phases; task-ID lines stripped from module docstrings
- 7 new env vars documented in .env.example (DevOps review finding, fixed)
- 4 new test files, 2 new warning-behavior tests added in fix commit
- Coverage: 96.27% (above 95% threshold), 3579 passed / 7 skipped (Gate #1)

**Review agents**: QA (self-conducted), DevOps ✓, Red-team ✓, Architecture ✓

**Review findings fixed**:
- DevOps: 7 new env vars not in .env.example — added DB pool tuning section and rate limit window entry (fix commit)
- Red-team: Rate limit window decoupled from /minute periods — added startup WARNING via model_validator + 2 new tests + vulture whitelist entry (fix commit)

**New advisories**: None — all review findings resolved in fix commit.

**Advisory count**: 6 open (ADV-P62-03, ADV-P63-01, ADV-P70-04, ADV-P71-01, ADV-P73-01, ADV-P73-02). ADV-P70-01 closed.

---

### [2026-03-29] Phase 73 — Test Quality Rehabilitation

**Tasks**: T73.1 (attack test enforcement gate), T73.2 (assertion density gate), T73.3 (parametrize rollout), T73.4 (import contract tests), T73.5 (fault injection integration tests), T73.6 (mapping module attack tests)

**Source**: Post-P71 retrospective — test suite had 410 shallow-only assertion violations, only 32 parametrize decorators, no mechanical enforcement of attack-test coverage.

**Delivered**:
- 4 Crucible-pattern enforcement gates: attack test conftest plugin (Gate 1), assertion density meta-test (Gate 2), import contract tests (Gate 4), fault injection integration tests (Gate 5)
- 100 parametrize decorators (up from 32), eliminating copy-paste test proliferation
- 0 shallow-only assertion violations (down from 410), Constitution Priority 4 compliance
- Mapping module attack tests added (Gate 1 enforcement gap)
- Coverage: 96.19% (above 95% threshold), 3,470 passed / 7 skipped

**NOT met (waived per spec-challenger)**:
- Test-to-code LOC ratio: 4.01:1 (target ≤2.5:1) → ADV-P73-01
- Test function count: 3,473 (target ≤2,500) → waived, no advisory (count reduction is a long-term effort)
- File consolidation: 2 files merged (target ≥20) → waived, structural consolidation deferred

**Review agents**: QA ✓, DevOps ✓, Red-team ✓, Architecture ✓ — 0 BLOCKERs after fixes.

**Review findings fixed**: Gate 1/2 counter test assertion (QA), async collection pattern corrected (QA), dead code removed from fault injection (Architecture), import contract test scope clarified (Architecture), boundary audit redundant isinstance noted (boundary auditor ADVISORY — no fix required).

**New advisories**: ADV-P73-01 (LOC ratio 4.01:1 waived), ADV-P73-02 (Gate 2 assert-True detection gap accepted).

**Advisory count**: 7 open (ADV-P62-03, ADV-P63-01, ADV-P70-01, ADV-P70-04, ADV-P71-01, ADV-P73-01, ADV-P73-02). Below Rule 11 threshold of 8.

---

### [2026-03-29] Phase 72 — Exception Specificity & Router Safety Hardening

**Tasks**: T72.1 (router audit-write catches), T72.2 (lifecycle/TLS/synthesizer catches), T72.3 (retention/DP accounting catches), T72.4 (privacy catches), T72.5 (httpx connection pooling)

**Source**: Post-P71 retrospective — broad `except Exception` catches masking genuine bugs across 14 production files; httpx.Client lacking connection pooling in webhook delivery; CVE-2026-4539 in cryptography/pygments.

**Delivered**:
- Narrowed 50+ broad `except Exception` catches to specific types (`ValueError`, `OSError`, `RuntimeError`, etc.) across 14 production files
- Added `httpx.Client` context manager for connection pooling in webhook delivery (T72.5)
- Intentionally broad catches preserved with inline justification comments: dp_accounting (fail-closed safety), licensing QR (library fallback), privacy async bridge (asyncio.run bridge), webhook retry (network errors)
- Updated cryptography and pygments dependency pins to resolve CVE-2026-4539 and lock staleness
- 52 new tests (17 attack + 35 feature) for exception specificity semantics
- Coverage: 96.23% (above 95% threshold), 3,535 passed / 7 skipped

**Review agents**: QA ✓, DevOps ✓, Red-team ✓, Architecture ✓ — 0 BLOCKERs after fixes.

**Review findings fixed (4)**: lifecycle.py catch inconsistency (QA), retention.py audit catches not narrowed (Architecture), dp_accounting.py audit catches not narrowed (Architecture), privacy.py missing justification comment on broad catch (Red-team).

**Boundary audit**: PASS. 3 ADVISORYs noted (ADR-0037 stale line numbers, test-to-code ratio, assertion specificity) — no new advisories raised; existing open advisories unchanged.

**New advisories**: None.

**Advisory count**: 7 open (ADV-P62-03, ADV-P63-01, ADV-P70-01, ADV-P70-04, ADV-P71-01, ADV-P73-01, ADV-P73-02). Below Rule 11 threshold of 8.

---

### [2026-03-29] Phase 71 — Audit Coverage Completion & Polish

**Tasks**: T71.1 (audit events on 4 endpoints), T71.2 (audit CLI wiring), T71.3 (licensing tests), T71.4 (settings extraction), T71.5 (unified counter), T71.6 (test rename)

**Source**: Post-P70 retrospective (2026-03-29) — findings F1-F6 + advisories A1-A6.

**Review agents**: QA ✓, DevOps ✓, Red-team ✓, Architecture ✓ — 0 BLOCKERs after fixes.

**Review findings fixed**: CLI log-event unbounded strings → max_length=1024 (Red-team), compliance.py missing counter (Architecture), settings.py endpoint label method suffix removed (Architecture), invalid-signature licensing test added (QA), --audit-key omission documented (QA).

**Advisories closed**: ADV-P70-02 (CLI wired), ADV-P70-03 (counters unified). ADV-P70-01 updated (1025 LOC after extraction). New: ADV-P71-01 (Prometheus multiprocess mode).

**Advisory count**: 5 open (ADV-P62-03, ADV-P63-01, ADV-P70-01, ADV-P70-04, ADV-P71-01). Below Rule 11 threshold.

---

### [2026-03-29] Phase 70 — Structural Debt Reduction

**Tasks**: T70.1 (composite PK/FK), T70.2 (v1/v2 signature removal), T70.3 (memory-safe vault), T70.4 (settings sub-models), T70.5 (operational runbook), T70.6 (shim removal), T70.7 (path param bounds), T70.8 (audit ordering), T70.9 (audit failure counter)

**Source**: Senior Architect & Security Audit (2026-03-28) — findings C6, C11, C12 + advisory drain ADV-P68-03/04/05.

**Review agents**: QA ✓, DevOps ✓, Red-team ✓, Architecture ✓ — 0 BLOCKERs after fixes.

**Review findings fixed**: reflection.py FK dedup upgraded to full column tuples (Architecture+QA), sign_v1/sign_v2 renamed to private (QA), cryptography CVE-2026-34073 bumped to >=46.0.6 (DevOps), total_epochs/num_rows upper bounds added (Red-team), port range validation ge=1 le=65535 (Red-team), stale reflection.py comment removed (QA).

**Advisories closed**: ADV-P68-03 (path param bounds), ADV-P68-04 (audit ordering consistency), ADV-P68-05 (audit failure counter).

**New advisories**: ADV-P70-01 (settings.py LOC), ADV-P70-02 (CLI not wired), ADV-P70-03 (fragmented counters), ADV-P70-04 (composite FK integration test).

**Advisory count**: 6 open (ADV-P62-03, ADV-P63-01, ADV-P70-01 through ADV-P70-04). Below Rule 11 threshold.

---

### [2026-03-29] Phase 69 — Security Depth & Test Coverage

**Tasks**: T69.1 (DNS pinning), T69.2 (profiler PII mode), T69.3 (concurrent load tests), T69.4 (timeout simulation), T69.5 (webhook deliveries endpoint), T69.6 (compliance erasure IDOR), T69.7 (parquet_path sandbox)

**Source**: Senior Architect & Security Audit (2026-03-28) — findings C4, C5, C8, C10 + security advisories ADV-P68-01, ADV-P68-02.

**Review agents**: QA ✓, DevOps ✓, Red-team ✓, Architecture ✓ — 0 BLOCKERs.

**Review findings fixed**: SSRF error leaking private IPs in deliveries endpoint (DevOps+Red-team), `pinned_ips` dead data consumed at delivery (Architecture), double-serialized JSON in deliveries 404 (Architecture), DB pool exhaustion test added (QA), error_message sanitization via safe_error_msg() (QA), stale docstring corrected (QA).

**Security advisories closed**: ADV-P68-01 (compliance erasure IDOR → self-erasure only), ADV-P68-02 (parquet_path sandboxed to CONCLAVE_DATA_DIR).

**Advisory count**: 5 open (ADV-P62-03, ADV-P63-01, ADV-P68-03, ADV-P68-04, ADV-P68-05). Below Rule 11 threshold.

---

### [2026-03-28] Phase 68 — Critical Safety Hardening

**Tasks**: T68.1 (thread-safe Faker), T68.2 (admin IDOR), T68.3 (audit-before-destructive), T68.4 (health strict mode), T68.5 (bcrypt narrowing), T68.6 (input bounds), T68.7 (fail-open block)

**Source**: Senior Architect & Security Audit (2026-03-28) — findings C1, C2, C3, C7, C9 + ADV-P67-01, ADV-P67-02.

**Review agents**: QA ✓, DevOps ✓, Red-team ✓, Architecture ✓ — 0 BLOCKERs.

**Review findings fixed**: `.env.example` missing `CONCLAVE_HEALTH_STRICT` (arch+devops), health check "skipped" vs "ok" status mismatch (QA), OSError sanitization in config_validation.py (devops).

**Pre-existing findings logged as advisories**: compliance erasure IDOR (ADV-P68-01), parquet_path directory sandbox (ADV-P68-02), remaining unbounded path params (ADV-P68-03), audit ordering inconsistency (ADV-P68-04), missing Prometheus counter on audit-fail (ADV-P68-05).

**Judgment call**: Red-team found two pre-existing IDOR/sandbox issues (compliance erasure, parquet_path). These are not P68 regressions — they predate this phase. Logged as advisories for Phase 69 rather than blocking P68 merge. ADV-P68-01 (compliance erasure IDOR) is SECURITY-tagged per Rule 26 (TTL: Phase 70).

**Advisory count**: 7 open (ADV-P62-03, ADV-P63-01, ADV-P68-01 through ADV-P68-05). Below Rule 11 threshold of 8.

---

### [2026-03-28] Phase 67 — Advisory Drain: Input Validation & Error Mapping Hardening

**Tasks**: T67.1 (settings key max_length), T67.2 (passphrase max_length), T67.3 (TLS error mapping)

**Review agents**: QA ✓, DevOps ✓, Red-team ✓, Architecture ✓ — 0 BLOCKERs.

**QA findings fixed**: docstring drift on TLSCertificateError (500→400), weak len()>0 assertions strengthened to exact values, missing DELETE boundary test added.

**New advisories**: ADV-P67-01 (systematic unbounded field lengths), ADV-P67-02 (rate_limit_fail_open warns-only).

**Advisory count**: 4 open (ADV-P62-03, ADV-P63-01, ADV-P67-01, ADV-P67-02). Below Rule 11 threshold.

---

### [2026-03-28] Phase 66 — Expired Security Advisory Resolution

**Tasks**: T66.1–T66.6

**Summary**: Resolved 3 expired security advisories (Rule 26 compliance), fixed CRITICAL
PII logging vulnerability, fixed correctness bug in privacy accountant, documented
single-operator assumption. Closed 4 advisories (ADV-P62-01, ADV-P62-02, ADV-P63-03,
ADV-P63-05). Open advisory count: 2 (ADV-P62-03, ADV-P63-01).

**Closed by code change:**
1. ADV-P62-01: OpenAPI docs disabled in production mode (`docs_url=None`, `redoc_url=None`, `openapi_url=None`). Exempt paths updated.
2. ADV-P62-02: Trusted proxy validation — `trusted_proxy_count` setting (zero-trust default). XFF ignored unless configured. IP format validation via `ipaddress.ip_address()`.
3. ADV-P63-05: Pygments confirmed dev-only transitive dependency; not in production Docker image. ADR-0061 documents mitigation.
4. ADV-P63-03: Single-operator privacy ledger assumption documented in ADR-0062. Code comments at both ledger queries.

**Additional fixes:**
5. T66.1 (CRITICAL): Operator username removed from INFO/WARNING logs in `auth.py`. Replaced with keyed HMAC-SHA256 identifier (truncated to 12 hex chars). Username `max_length=255` added to prevent DoS.
6. T66.5: `scalar_one()` in `accountant.py` wrapped in `LedgerNotFoundError` (both `spend_budget` and `reset_budget`). HTTP mapping to 404. Ledger ID excluded from HTTP response body.

**Review findings:**
- FINDING (architecture): `LedgerNotFoundError` missing from `_OPERATOR_ERROR_HANDLERS` — fixed by deriving handler list from `OPERATOR_ERROR_MAP.keys()`. Also fixed pre-existing gap (15 map entries, only 9 handlers). Regression test added.
- ADVISORY: `CONCLAVE_TRUSTED_PROXY_COUNT` added to `.env.example`.

**Lessons learned:**
- Exception handler registration has two separate data structures (map + handler list) that can diverge silently. The derived-from-map fix permanently eliminates this drift.
- The developer subagent repeatedly re-ran the full test suite after encountering failures instead of fixing and re-running targeted tests. Future phases should instruct: "fix failures, run ONLY affected files, then commit."

---

### [2026-03-27] Phase 65 — Advisory Drain & Polish

**Tasks**: T65.1 (advisory drain), T65.2 (polish batch)

**Summary**: Drained advisory backlog from 15 → 6 open items (below Rule 11 cap of 8).

**Closed by code change (T65.1)**:
1. ADV-P58-01: Deleted dead `_sign_v1/_sign_v2/_sign_v3` wrapper methods from AuditLogger; updated test_audit_v3_hmac_attack.py to use standalone sign_v1/sign_v2 functions; removed Category S from vulture whitelist.
2. ADV-P58-02: Added `UNEXPECTED_WEBHOOK_ERRORS_TOTAL` Prometheus counter to wiring.py CRITICAL exception branch.
3. ADV-P62-04: Added `WEBHOOK_DELIVERIES_SKIPPED_TOTAL` counter to webhook_delivery.py with `reason="circuit_open"` label.
4. ADV-P63-04: Pre-initialized `RATE_LIMIT_REDIS_FALLBACK_TOTAL` with all 4 tier labels ("unseal", "auth", "download", "general").
5. ADV-P64-02: Amended ADR-0058 to document T64.3 rate_limit decomposition pattern.

**Closed by documented acceptance (T65.1)**:
- ADV-P59-01: CI SBOM documents dev-only subset; synthesizer deps not in production image.
- ADV-P59-02: asyncio.run() in threadpool is accepted tradeoff per ADR-0059 assessment.
- ADV-P59-04: FastAPI default 422 handler covers validation errors; no custom 400 needed.
- ADV-P61-01: Infrastructure marker exists; CI coverage split deferred to when test-infra phase occurs.
- ADV-P63-02: Duplicate of ADV-P62-02.

**Polish batch (T65.2)**:
- `_IP_KEYED_PATHS` dead constant removed from rate_limit.py and its `__all__` entry.
- `MemoryStorage` removed from rate_limit_backend.__all__ (no longer re-exported).
- `_memory_hit(limit: object, ...)` type erasure fixed to `limit: RateLimitItem` with module-scope import.
- DEVELOPER_GUIDE.md: "after Phase 43" updated to "after Phase 64".
- REQUEST_FLOW.md: DI wiring section updated from jobs.tasks shim to canonical job_orchestration path.

---

### [2026-03-27] Phase 64 — Review Summary

**Reviewers**: QA (pending), DevOps, Architecture, Red-team

**Verdicts**: Arch — FINDING (2 cosmetic, batched); DevOps — FINDING (1 CVE, fixed); Red-team — PASS

**FINDINGs fixed** (`569e7a2`):
1. CVE-2026-34073 in cryptography 46.0.5 → updated to 46.0.6 (DevOps)

**Cosmetic findings (batched, not standalone fixes)**:
- `limit: object` type erasure in rate_limit_backend.py (Arch) — minor type safety
- `_IP_KEYED_PATHS` dead constant in rate_limit.py (Arch) — dead code
- `MemoryStorage` dead re-export in rate_limit_backend.__all__ (Arch) — cleanup

**ADVISORIEs** (logged, not blocking):
- ADV-P64-01: Advisory count at 15, exceeds Rule 11 cap of 8 — drain sprint needed before P65 (DevOps)
- ADV-P64-02: ADR-0058 should be amended to cover T64.3 rate_limit decomposition pattern (Arch)

---

---

*Phases 1-63 archived to `docs/retro_archive/`. Phase retrospectives available in git history.*
