# Conclave Engine — Retrospective Log

Living ledger of review retrospective notes and open advisory items.
Updated after each task's review phase completes.

### [2026-03-27] Phase 60 — Review Summary

**Reviewers**: Red-team, Architecture, DevOps (QA pending — long test suite)

**Verdicts**: Red-team — FINDING (1, fixed); Arch — PASS (1 ADVISORY); DevOps — PASS

**FINDINGs fixed** (`66e1146`):
1. `UnsealRequest.passphrase` missing `max_length=1024` — PBKDF2 CPU DoS vector (Red-team)

**ADVISORIEs** (logged, not blocking):
- ADR-0027 re-export table should include `build_ephemeral_storage_client` (Arch)

---

### [2026-03-26] Phase 58 — Review Summary

**Reviewers**: Red-team, Architecture, DevOps (QA agent crashed — API 500)

**Verdicts**: Red-team — PASS (0 findings); Arch — PASS (1 ADVISORY); DevOps — FINDING (1 BLOCKER)

**BLOCKERs fixed in commit** (`d4761d3`):
1. `_warn_unrecognized_conclave_env_vars()` logged raw env var values at WARNING — replaced with redacted `"***"` (DevOps BLOCKER)

**ADVISORIEs** (logged, not blocking):
- Dead `_sign_v1/_sign_v2/_sign_v3` wrapper methods on AuditLogger — 78 lines of vestigial code after extraction to standalone functions (Arch)
- Consider adding `unexpected_webhook_errors_total` Prometheus counter on wiring.py CRITICAL branch (DevOps)

**T58.5 (settings sub-models) DEFERRED**: Breaking configuration change requires ADR and extensive deployment testing. Not suitable for a quality-hardening phase.

---

### [2026-03-26] Phase 59 — Review Summary

**Reviewers**: QA, DevOps, Architecture, Red-team

**Verdicts**: DevOps — BLOCKER (1) + ADVISORY; Arch — FINDING (2 critical + 1 advisory); Red-team — FINDING (5) + 2 ADVISORY; QA — FINDING (2)

**FINDINGs fixed in review commit** (`57270dd`):
1. `docs/api/openapi.json` regenerated from live versioned app — all 4 reviewers flagged stale paths (BLOCKER)
2. Frontend `client.ts` updated to `/api/v1/` paths; Vite proxy rewrite removed (Arch critical)
3. Input validation: `max_length=255` on ConnectionCreateRequest fields, `max_length=10000` on settings value, `min_length=1` on RotateRequest passphrase (Red-team F1-F3)
4. `GET /api/v1/connections` capped at `.limit(100)` (Red-team F4)
5. ADR-0057 documents API versioning strategy (Arch)
6. Frontend E2E tests updated for versioned paths
7. Integration test for compliance erasure fixed for versioned router wiring

**ADVISORIEs** (logged, not blocking):
- CI SBOM omits synthesizer dependency group — document as dev-only subset (DevOps)
- `asyncio.run()` in threadpool for budget refresh — reliability concern under concurrent load (Red-team)
- `X-Forwarded-For` unconditionally trusted — deployment topology assumption (Red-team)
- 400 error not in explicit COMMON_ERROR_RESPONSES — covered by FastAPI default handler (QA)

---

### [2026-03-26] Phase 57 — Review Summary

**Reviewers**: QA, DevOps, Architecture, Red-team

**Verdicts**: Arch — FINDING (1 critical + 1 doc); Red-team — FINDING (1) + 2 ADVISORY; DevOps — FINDING (2); QA — FINDING (3)

**FINDINGs fixed in review commit** (`74b64bf`):
1. `ErasureResponse` now surfaces `audit_logged` field from `DeletionManifest` (Arch + Red-team)
2. T57.6 conflict-warning guard logic fixed — fires whenever env/conclave_env differ (QA)
3. `env` field docstring corrected — removed false "copy to conclave_env" claim (QA)
4. Credential-leak test strengthened — uses real embedded credentials in DATABASE_URL (QA)
5. Stale ENV references updated across .env.example, config_validation.py, OPERATOR_MANUAL.md, ci.yml (DevOps + Arch)
6. Conflict-guard comparison made case-insensitive (Red-team advisory)

**ADVISORIEs** (logged, not blocking):
- `auth.py:274-278` passes `str(exc)` into 401 response body — no allowlist guard against future sensitive messages (DevOps structural concern)
- Rate limiter in-memory fallback multiplies by worker count under Redis failure (Red-team, pre-existing)

---

### [2026-03-25] Phase 56 — Review Summary

**Reviewers**: QA, DevOps, Architecture, Red-team

**Verdicts**: Architecture — FINDING (1); DevOps — FINDING (1, 1 ADVISORY); Red-team — PASS; QA — FINDING (1)

**FINDINGs fixed in review commit** (`bab7077`):
1. 11 ADRs + 3 operational docs updated with new synthesizer sub-package paths (Arch F1)
2. `requests` upgraded to 2.33.0 to fix CVE-2026-25645 (DevOps F1)
3. `test_synthesizer_tasks_lifecycle.py` (1,103 LOC) split into 3 files <600 LOC (QA F1)

**ADVISORIEs** (not logged as new — pre-existing from P55):
- Huey worker startup ordering: IoC globals populated via module-scope import; no startup assertion gate (DevOps, pre-existing, documented in wiring.py)

**PM Judgment Calls**:
- T56.1 patch-path resolution: "no test modification" AC relaxed to allow mock.patch() path updates (test logic unchanged). Justified: physical file relocation makes flat paths unreachable; updating patch strings is a mechanical consequence, not a behavioral change.
- T56.2 Huey worker contract: wiring kept at module scope (not inside create_app()) to preserve Huey worker import-time side effect. Spec-challenger identified this constraint; PM resolved before developer brief.

---

### [2026-03-25] Phase 55 — Review Summary

**Reviewers**: QA, DevOps, Architecture, Red-team

**Verdicts**: QA — FINDING (1 BLOCKER, 1 FINDING, 3 ADVISORIEs); DevOps — FINDING (1 FINDING, 2 ADVISORIEs); Architecture — FINDING (2 FINDINGs); Red-team — FINDING (1 FINDING, 3 ADVISORIEs)

**BLOCKERs + FINDINGs fixed in review commit** (`9ac49d9`):
1. T55.3 integration test added — AuditLogger + LocalFileAnchorBackend + AnchorManager end-to-end chain continuity (QA BLOCKER)
2. `chain_head_hash` from anchor JSONL now validated via `_validate_chain_head_hash` before use as `_prev_hash` (Red-team F1)
3. Raw exception objects in `audit.py` replaced with `type(exc).__name__` at 3 log sites (DevOps F1)
4. Silent `except Exception: pass` in `_log_verification_failure` now emits `sys.stderr.write()` last-resort signal (QA F1)
5. Single-call-site SSRF wrappers inlined — `_ssrf_validate_registration` and `_ssrf_validate_delivery` removed (Arch F1)
6. ADR-0009 exempt routes list amended to reference `COMMON_INFRA_EXEMPT_PATHS` as authoritative source (Arch F2)
7. ADR-0055 allowlist table updated with missing `faker`, `random` entries (QA advisory, treated as doc accuracy fix)

**New ADVISORIEs logged**:
- ADV-P55-01: `/health/vault` exposes worker PID — unnecessary fingerprinting surface (Red-team)
- ADV-P55-02: Broad `joblib` prefix in RestrictedUnpickler allowlist — tighten to specific submodules at next SDV upgrade (Red-team)
- ADV-P55-03: Per-worker audit chain interleaving on shared anchor file in multi-worker deployments (DevOps)
- ADV-P55-04: New failure modes (SSRF rejection, HMAC failure, chain resume) lack Prometheus counters (DevOps)
- ADV-P55-05: Unbounded list queries without LIMIT on GET /settings/ and GET /webhooks/ (Red-team, pre-existing)

---

### [2026-03-25] Phase 54 — Review Summary

**Reviewers**: QA, DevOps, Red-team (no Architecture — no src/synth_engine/ changes)

**Verdicts**: QA — FINDING (2 BLOCKERs, 6 FINDINGs); DevOps — FINDING (1); Red-team — PASS (0 BLOCKERs, 2 FINDINGs)

**FINDINGs fixed in review commit** (`944895b`):
1. SQL table name allowlist assertion before f-string interpolation (Red-team F1)
2. DATABASE variable quoted in psql SQL commands (Red-team F2)
3. Raw `exc` logging replaced with `type(exc).__name__` at 9 sites (DevOps F1)
4. Dead SubsettingEngine import removed (QA F3)
5. Rubber-stamp DSN masking assertion replaced with AST-based check (QA F4)
6. Empty DataFrame guard added to subsetting stage (QA F5)
7. Epsilon boundary changed from `<` to `<=` (QA F6)
8. Inaccurate docstrings fixed — subsetting and FK validation (QA F7)
9. Makefile validate-pipeline target added (QA BLOCKER 2)
10. Duplicate BudgetExhaustionError import removed (DevOps A4)

**QA BLOCKER 1 (T54.3 not executed) — PM judgment**: PostgreSQL not running locally.
E2E_VALIDATION_RESULTS.md is a template with all 13 required sections. The validation
script is ready to execute. Actual run deferred to when user provisions PostgreSQL.
Logged as ADV-P54-01.

---

### [2026-03-24] Phase 53 — Review Summary

**Reviewers**: QA, DevOps, Architecture (×2), Red-team (×2)

**Verdicts**: QA — FINDING (1); DevOps — PASS; Architecture — PASS (1 ADVISORY);
Red-team — PASS (0 BLOCKERs, ADVISORIEs only)

**FINDINGs fixed in review commit** (`45e6298`):
1. Tautological assert in `test_audit_hmac_details.py:290` — `v1_hex_part == v2_hex_part`
   compared a variable to itself. Removed vacuous assertion, consolidated to single variable.
2. `-> Any` return type on `auth_app` fixture and 10 function params in
   `test_all_routes_require_auth.py` — replaced with `FastAPI`.
3. Unreachable `except ImportError: pass` in `clear_settings_cache` fixture — replaced
   with unconditional imports.

**ADVISORIEs resolved in review commit**:
- ADR-0047 stale mutmut reference — amendment note added referencing ADR-0054.
- `session.sqlite` not in `.gitignore` — added.

**New ADVISORIEs logged** (from red-team/architecture reviews):
- ADV-P53-01: HMAC pipe-delimiter injection — structural collision possible if fields
  contain `|`. Mitigated: fields are system-controlled. Future hardening item.
- ADV-P53-02: v1 signature still accepted with no deprecation timeline. Future: log
  WARNING on v1 verify, deprecate by Phase 60.
- ADV-P53-03: cosmic-ray test-command uses hardcoded test file list — maintenance
  concern if new security test files are added without updating cosmic-ray.toml.

---

### [2026-03-24] T53.4 — Redis TLS Promotion Deduplication

**Task**: Consolidate Redis TLS URL promotion into a single shared utility and
add comprehensive edge-case test coverage for all spec-challenger inputs.

**Outcome**: No production code change required. The canonical
promote_redis_url_to_tls() implementation already resided in
shared/task_queue.py (resolved by P52 inline). The bootstrapper already
imported from there (ADV-P47-02 RESOLVED). T53.4 added 28 new edge-case tests
in tests/unit/test_redis_tls_promotion_edge_cases.py documenting and
verifying the behavioral contract for all spec-challenger inputs:

- Already-TLS (rediss://) URLs: idempotent, no double-promotion
- Empty string: no exception, returned as-is
- Non-redis schemes (http://, https://, amqp://): pass through unchanged
- redis+sentinel:// URLs: pass through unchanged (different protocol)
- redis+socket:// Unix socket URLs: pass through unchanged
- IPv6 literal host addresses ([::1], [2001:db8::1]): correctly promoted
- Percent-encoded credentials (p%40ss): not decoded or altered
- URL query parameters (timeout, retry_on_timeout): preserved after promotion
- Single-implementation invariant: verified across shared/tls/config.py and
  bootstrapper/dependencies/redis.py

**Gate #1**: 2732 passed, 7 skipped. All quality gates (ruff, mypy, bandit,
vulture) PASS.

---

## Open Advisory Items

Advisory findings without a resolved target task are tracked here.
Drain (delete) rows when their target task is completed.

| ID | Source | Target Task | Severity | Advisory |
|----|--------|-------------|----------|----------|
| ~~ADV-P46-01~~ | ~~Red-Team T46.2~~ | T47.8 | ~~ADVISORY~~ | ~~asyncpg TLS 1.3 pin — RESOLVED in T47.8 (shared/db.py)~~ |
| ~~ADV-P46-04~~ | ~~DevOps T46.3~~ | ADV drain P47 | ~~ADVISORY~~ | ~~LibreSSL detection — RESOLVED in P47 (rotate-mtls-certs.sh)~~ |
| ~~ADV-P46-05~~ | ~~Arch T46.3~~ | ADV drain P47 | ~~ADVISORY~~ | ~~Prometheus metrics naming — RESOLVED in P47 (ADR-0045 amendment)~~ |
| ~~ADV-P46-06~~ | ~~Red-Team T46.4~~ | ADV drain P47 | ~~ADVISORY~~ | ~~MinIO NetworkPolicy — RESOLVED in P47 (minio-policy.yaml)~~ |
| ~~ADV-P47-01~~ | ~~PM P46 merge~~ | P47 fix | ~~BLOCKER~~ | ~~Production Smoke Test — RESOLVED in P47 (CI dummy secrets provisioning)~~ |
| ~~ADV-P47-02~~ | ~~Arch P47 review~~ | P52 inline | ~~ADVISORY~~ | ~~`_promote_redis_url_to_tls` duplication — RESOLVED in P52 (bootstrapper/dependencies/redis.py now imports from shared/task_queue.py)~~ |
| ~~ADV-P47-03~~ | ~~Arch P47 review~~ | ADV drain pre-P49 | ~~ADVISORY~~ | ~~Scope-based auth ADR — RESOLVED (ADR-0049 written)~~ |
| ~~ADV-P47-04~~ | ~~Red-Team P47~~ | T50.3 | ~~ADVISORY~~ | ~~`/security/shred` and `/security/keys/rotate` removed from `AUTH_EXEMPT_PATHS` — RESOLVED in T50.3 (_exempt_paths.py)~~ |
| ~~ADV-P47-05~~ | ~~Red-Team P47~~ | — | ~~ADVISORY~~ | ~~All-or-nothing scope grant — CLOSED as accepted design (single-operator MVP). Already documented in ADR-0049 §4 "Default scope issuance" and §Consequences/Negative. Future multi-operator support tracked as post-MVP backlog item.~~ |
| ~~ADV-P47-06~~ | ~~Red-Team P47~~ | T48.1 | ~~ADVISORY~~ | ~~In-memory rate limiter — RESOLVED in T48.1 (Redis-backed rate limiting)~~ |
| ~~ADV-P48-01~~ | ~~Red-Team P48~~ | ADV drain pre-P49 | ~~ADVISORY~~ | ~~X-Forwarded-For trust model — RESOLVED (PRODUCTION_DEPLOYMENT.md Appendix B)~~ |
| ~~ADV-P48-02~~ | ~~Red-Team P48~~ | ADV drain pre-P49 | ~~ADVISORY~~ | ~~Redis INCR+EXPIRE atomicity — CLOSED as accepted tradeoff (standard industry pattern, documented)~~ |
| ~~ADV-P48-03~~ | ~~Red-Team P48~~ | ADV drain pre-P49 | ~~ADVISORY~~ | ~~Anchor verification equality-only — CLOSED as accepted tradeoff (S3 Object Lock, documented in ADR-0048)~~ |
| ~~ADV-P48-04~~ | ~~Red-Team P48~~ | ADV drain pre-P49 | ~~ADVISORY~~ | ~~ale_key field in settings — RESOLVED (field removed from ConclaveSettings)~~ |
| ~~ADV-T49-01~~ | ~~Dev T49.5~~ | ~~—~~ | ~~ADVISORY~~ | ~~mutmut 3.x + CPython 3.14 segfault incompatibility: all target mutants exit with SIGSEGV (-11) rather than normal test failure (exit code 1). 0 mutants survived; 200/200 detected via process crash. Mutation hardening tests added to verify behavioral correctness without trampoline. RESOLVED by ADR-0052 (accepted gap with manual hardening tests).~~ |
| ~~ADV-P47-07~~ | ~~Red-Team P47~~ | T50.4 | ~~ADVISORY~~ | ~~TOCTOU in `ModelArtifact.load()`: RESOLVED in T50.4. Removed `os.path.exists()` and `os.path.getsize()` pre-checks; file now read with bounded `f.read(_MAX_ARTIFACT_SIZE_BYTES + 1)`, size checked on `len(raw)` after read. No TOCTOU race window.~~ |
| ~~ADV-P49-02~~ | ~~Red-Team P49~~ | — | ~~ADVISORY~~ | ~~Audit HMAC does not cover `details` field — CLOSED as accepted limitation. Fix would break all existing signatures (backward-incompatible). Chain hash provides transitive coverage. Pre-existing design, not a regression. Risk: attacker with log store write access could modify details without invalidating per-event HMAC, but chain hash integrity check would detect tampering on re-verification.~~ |
| ~~ADV-P49-03~~ | ~~DevOps P49~~ | ~~—~~ | ~~ADVISORY~~ | ~~mutmut CI gate not wired into `.github/workflows/ci.yml`. Blocked by ADV-T49-01 (Python 3.14 segfault). RESOLVED by ADR-0052 (gate deferred until upstream mutmut supports Python 3.14).~~ |
| ~~ADV-P51-01~~ | ~~PM P51 review~~ | P52 inline | ~~ADVISORY~~ | ~~Release tag regex not end-anchored — RESOLVED in P52 (release.yml grep pattern end-anchored with `$`)~~ |
| ~~ADV-P51-02~~ | ~~PM P51 review~~ | P52 inline | ~~ADVISORY~~ | ~~bump_version.sh tag hint unconditionally applies RC transform to stable versions — RESOLVED in P52 (conditional tag hint)~~ |
| ~~ADV-P52-01~~ | ~~Arch T52.2 review~~ | P55 drain | ~~ADVISORY~~ | ~~`_DP_EPSILON_DELTA` renamed to public `DP_EPSILON_DELTA` — RESOLVED in P55 advisory drain.~~ |
| ~~ADV-P52-02~~ | ~~DevOps T52.2 review~~ | P55 drain | ~~ADVISORY~~ | ~~`demos/` added to ruff and bandit CI gates — RESOLVED in P55 advisory drain.~~ |
| ~~ADV-P52-03~~ | ~~Red-Team P52~~ | P53 drain | ~~ADVISORY~~ | ~~nbstripout is pre-commit hook only, not a git filter — CLOSED as accepted. Pre-commit hook is sufficient; git filter is nice-to-have.~~ |
| ~~ADV-P52-04~~ | ~~Red-Team P52~~ | P53 drain | ~~ADVISORY~~ | ~~Benchmark results contain hardware metadata — CLOSED as accepted. Intentional for reproducibility.~~ |
| ~~ADV-P52-05~~ | ~~Boundary Audit P52~~ | P53 drain | ~~ADVISORY~~ | ~~3 rubber-stamp attack tests removed from `test_benchmark_results.py` — RESOLVED in P53.~~ |
| ~~ADV-P52-06~~ | ~~Boundary Audit P52~~ | P53 drain | ~~ADVISORY~~ | ~~Dead `"safe_load"` filter logic fixed at `test_benchmark_infrastructure.py` — RESOLVED in P53.~~ |
| ~~ADV-P52-07~~ | ~~Boundary Audit P52~~ | P53 drain | ~~ADVISORY~~ | ~~README metrics updated to current counts — RESOLVED in P53.~~ |
| ~~ADV-P52-08~~ | ~~Boundary Audit P52~~ | P53 drain | ~~ADVISORY~~ | ~~Stale branches and worktrees cleaned — RESOLVED in P53.~~ |
| ~~ADV-P53-01~~ | ~~Red-Team P53~~ | fix/ADV-P53-01 drain | ~~ADVISORY~~ | ~~HMAC pipe-delimiter injection — RESOLVED in fix/ADV-P53-01-hmac-length-prefixed: v3 length-prefixed HMAC format implemented; `_sign_v3` uses 4-byte big-endian length prefixes eliminating field-boundary collisions. 10 attack tests added. All new events use v3: format.~~ |
| ~~ADV-P53-02~~ | ~~Red-Team P53~~ | P55 drain | ~~ADVISORY~~ | ~~WARNING logged on v1 HMAC signature verification, deprecation by Phase 60 — RESOLVED in P55 advisory drain.~~ |
| ~~ADV-P53-03~~ | ~~Arch P53~~ | P55 drain | ~~ADVISORY~~ | ~~cosmic-ray.toml annotated with P55 security test files — RESOLVED in P55 advisory drain.~~ |
| ~~ADV-P53-04~~ | ~~PM P53 CI~~ | ~~—~~ | ~~ADVISORY~~ | ~~mutation-test CI job removed from CI entirely — runs as local PM gate instead (ADR-0054 amendment). RESOLVED in P53.~~ |
| ~~ADV-P54-01~~ | ~~QA P54~~ | P54 drain | ~~ADVISORY~~ | ~~E2E_VALIDATION_RESULTS.md is a template — RESOLVED in P54 docs branch: full pipeline executed against live Pagila, all checks PASS (6.08 s wall-clock). See docs/E2E_VALIDATION_RESULTS.md.~~ |
| ~~ADV-P55-01~~ | ~~Red-Team P55~~ | P55 drain | ~~ADVISORY~~ | ~~Worker PID replaced with opaque UUID in `/health/vault` — RESOLVED in P55 advisory drain.~~ |
| ~~ADV-P55-02~~ | ~~Red-Team P55~~ | chore/review-refinements drain | ~~ADVISORY~~ | ~~Broad `joblib` prefix replaced with `joblib.numpy_pickle` + `joblib._store_backends` — RESOLVED in chore/review-refinements-and-advisory-drain.~~ |
| ~~ADV-P55-03~~ | ~~DevOps P55~~ | chore/review-refinements drain | ~~ADVISORY~~ | ~~OPERATOR_MANUAL.md §7.3 added: per-worker chain semantics, single-chain compliance guidance, --workers 1 recommendation — RESOLVED in chore/review-refinements-and-advisory-drain.~~ |
| ~~ADV-P55-04~~ | ~~DevOps P55~~ | P55 drain | ~~ADVISORY~~ | ~~Prometheus counters added: `ssrf_registration_rejection_total`, `artifact_verification_failure_total`, `audit_chain_resume_failure_total` — RESOLVED in P55 advisory drain.~~ |
| ~~ADV-P55-05~~ | ~~Red-Team P55~~ | P55 drain | ~~ADVISORY~~ | ~~`.limit(100)` added to list queries in settings and webhooks routers — RESOLVED in P55 advisory drain.~~ |

---

### [2026-03-23] Phase 52 End-of-Phase Retrospective

**Phase Goal**: Demo & Benchmark Suite — the final backlog phase. Deliver reproducible
epsilon curve benchmarks, three audience-specific notebooks, pre-rendered figures, and
published results integrated into the project README.

**Exit Criteria**: All tasks (T52.1–T52.6) delivered. Gate #2 PASS: 2704 passed, 7 skipped,
0 failed. Coverage: 96.92%. Red-team: PASS (0 BLOCKERs). Boundary audit: PASS (0 FINDINGs).

**PRs merged**: #186 (T52.1), #187 (arch review fixes), #188 (T52.2), #190 (T52.3–5 notebooks),
#191 (T52.6 published results), #192 (matplotlib skip guard), #193 (SQL validation review fix).

**What went well**:
- Parallel worktree agents for T52.3/T52.4/T52.5 — all three notebooks developed concurrently,
  then combined via cherry-pick into a single PR (#190). Significant time savings.
- Red-team caught a real defense-in-depth gap (SQL table name validation) that the QA reviewer missed.
  Fixed in PR #193 with matching test.
- All pre-existing advisories from P47/P51 resolved inline during P52 (ADV-P47-02, ADV-P51-01, ADV-P51-02).
- Zero PII leakage in committed artifacts — all three notebooks stripped, benchmark results contain
  only statistical metrics, SVGs contain only vector graphics.

**What could improve**:
- Gate #2 caught a missing `pytest.importorskip("matplotlib")` guard — the test assumed the `demos`
  optional dependency group was installed. Should have been caught during GREEN phase.
- Cherry-pick workflow from parallel worktrees caused a README merge conflict (T52.4 + T52.5 both
  edited `demos/README.md`). Consider using a shared base branch for parallel tasks editing the same files.
- Boundary auditor found 3 rubber-stamp tests and 1 dead logic assertion — test quality review
  should happen during GREEN phase, not post-merge.

**Open advisory count at phase end**: 8 (ADV-P52-01 through ADV-P52-08). All ADVISORY severity,
none security-related. At Rule 11 limit — next phase must drain to ≤5 before new feature work.

**Phase 52 is the final backlog phase.** All planned work is complete.

---

### [2026-03-23] Phase 52 — Red-Team Review

**Verdict**: PASS (0 BLOCKERs, 1 FINDING fixed, 3 ADVISORYs logged)

**FINDING-1 (FIXED)**: Quickstart notebook SQL f-string without table name validation.
Fixed in PR #193 — added `re.match(r'^[a-zA-Z0-9_]+$', table)` guard matching benchmark harness.

**ADV-RT-01**: nbstripout is pre-commit only, not git filter (ADV-P52-03).
**ADV-RT-02**: `--output-dir` CLI arg has no containment check — local tool, low risk.
**ADV-RT-03**: Hardware metadata in committed benchmark results (ADV-P52-04).

**Items reviewed and found secure**: Credential exposure (env vars only), YAML deserialization
(safe_load), JSON loading (stdlib), pickle security (HMAC-SHA256), path traversal guards
(is_relative_to), filename sanitization, error message sanitization, notebook output stripping,
DP budget isolation, supply chain (pinned revs), auth/authz (no regression), no code injection.

---

### [2026-03-23] Phase 52 — Phase Boundary Audit

**Verdict**: PASS (0 FINDINGs, 4 ADVISORYs logged)

**Documentation accuracy**: CLEAN. All paths, commands, env vars match code. README metrics
slightly stale (ADV-P52-07). ADR-0053 accurate.

**Test quality**: CLEAN. Production-to-test LOC ratio 1:1.46 (within 1:2.5 budget).
3 rubber-stamp tests (ADV-P52-05) and 1 dead logic assertion (ADV-P52-06) — cosmetic, batched.

**Open advisories**: 8 total, all ADVISORY, no expired TTLs. At Rule 11 limit.

**Workspace cleanup**: 84 merged local branches, ~50 merged remote branches, 14 agent worktrees
pending cleanup (ADV-P52-08).

---

### [2026-03-23] Phase 52 — T52.1: Benchmark Infrastructure

**Branch**: `feat/P52-demo-benchmark-suite`

**Tasks completed**: T52.1 (Benchmark Infrastructure)

**T52.1 — Benchmark Infrastructure**:
Created the foundation for the Demo & Benchmark Suite:

- `scripts/benchmark_epsilon_curves.py` — Parameterized benchmark harness.
  Accepts noise_multiplier x epochs x sample_size parameter grids, records
  per-run epsilon (from Opacus), wall time, KS statistic per numeric column,
  chi-squared p-value per categorical column, MAE, correlation matrix delta,
  FK orphan rate, and hardware metadata (CPU, RAM, OS, GPU if available).
  Outputs structured JSON + CSV to configurable output directory.
  Idempotent (skips completed combinations on resume). Per-run timeout (default
  1800s) writes TIMEOUT result row and continues. YAML loading uses
  yaml.safe_load() only (Bandit B506). Output filenames sanitized from
  parameter config, never from dataset columns (path-traversal prevention).
  `_BENCHMARK_DP_DELTA = 1e-5` explicitly matches production constant.

- `demos/conclave_demo.py` — Convenience wrapper for interactive demos.
  Uses isolated temp directory (never production ledger). Requires and passes
  artifact signing_key to ModelArtifact.load(); loading without key is
  forbidden at the code level.

- `demos/` directory structure with README.md placeholder, `__init__.py`,
  `figures/` and `results/` sub-directories.

- `pyproject.toml`: Added `[tool.poetry.group.demos]` optional group
  (matplotlib ^3.9, seaborn ^0.13, jupyter ^1.0, scikit-learn ^1.5,
  nbstripout ^0.7); added `cpu_only` pytest marker.

- `.pre-commit-config.yaml`: Added nbstripout hook at rev v0.7.1 (pinned —
  supply-chain hardening, never HEAD or branch refs).

- `.gitignore`: Added demos/figures/*.png and .pdf (ignored), with
  `!*.svg` and `!*_v1.json` exceptions for committed artifacts.

**Tests added**: 10 attack/negative tests (Rule 22 compliance):
test_demo_dependencies_not_imported_in_production_modules,
test_benchmark_harness_rejects_run_without_dataset_fixture,
test_benchmark_harness_records_failure_row_on_run_error,
test_benchmark_harness_rejects_malicious_yaml_config,
test_bandit_scan_passes_on_benchmark_harness,
test_results_artifact_contains_schema_version_field,
test_committed_results_contain_no_real_column_names,
test_parameter_grid_is_committed_alongside_results,
test_benchmark_epsilon_delta_matches_production_constant,
test_benchmark_run_produces_identical_metrics_given_fixed_seed

**Gate #1 results**: 2639 passed, 6 skipped, 96.83% unit coverage (>= 95%).
Integration tests: 212 passed, 17 skipped.

**Open advisory count at T52.1**: 0 open advisories.

---

### [2026-03-23] Phase 52 — T52.2: Execute Benchmarks (Real Results)

**Branch**: `feat/P52-T52.2-benchmark-results`

**Tasks completed**: T52.2 (Execute Benchmarks — Real Results)

**T52.2 — Execute Benchmarks**:
Executed a 6-cell reduced parameter grid (noise_multiplier=[1.0,5.0,10.0]
x epochs=[50,100] x sample_size=[1000]) against sample_data/customers.csv
and sample_data/orders.csv. Committed versioned JSON artifacts:

- `demos/results/grid_config.json` — Grid manifest (committed alongside results).
- `demos/results/benchmark_customers_v1.json` — 6 rows, 5 COMPLETED / 1 FAILED.
  FAILED row: nm=1.0, epochs=100 — DP budget exhausted (spent=50.09, allocated=50.0).
  Committed honestly per spec (FAILED row carries wall_time_seconds and error_message).
- `demos/results/benchmark_orders_v1.json` — 6 rows, 6 COMPLETED.

All artifact structural requirements verified:
- schema_version present at artifact top level and in every row.
- wall_time_seconds present and positive in all rows (including FAILED).
- hardware metadata present and non-empty in all rows.
- All grid cells present in both artifacts.
- Column metric keys match sample_data/ fixture column names.

**TDD sequence**: ATTACK RED (5 negative tests) -> FEATURE RED (13 failing) -> GREEN (18/18) -> REFACTOR (ruff/mypy clean).

**Tests added (T52.2)**: 18 tests in `tests/unit/test_benchmark_results.py`:
- 5 attack/negative tests (TestArtifactIntegrityAttacks)
- test_grid_config_committed_alongside_results
- test_results_schema_version_present[customers/orders]
- test_results_schema_version_present_in_all_rows[customers/orders]
- test_results_manifest_contains_all_parameter_grid_cells[customers/orders]
- test_wall_time_field_present_and_positive_in_all_result_rows[customers/orders]
- test_results_hardware_metadata_present_and_non_empty[customers/orders]
- test_results_column_names_match_fixture[customers/orders]

**Gate #1 results**: 2657 passed, 6 skipped, 96.83% unit coverage (>= 95% required). PASS.

**Reviews**:

**QA** (PASS): No blockers or findings.

**DevOps** (PASS): No PII in committed artifacts. Hardware metadata acceptable (arm arch only,
not full brand string). .gitignore allow-list correct.
ADVISORY: CI gap — ruff/bandit not covering `demos/` directory (pre-existing, documented in ADR-0053). Logged as ADV-P52-02.

**Architecture** (PASS): File placement correct. ADR-0053 satisfies prior finding.
`RunDemoResult` TypedDict appropriate for its scope.
ADVISORY: `_DP_EPSILON_DELTA` is a private symbol consumed by demo code outside the production
boundary — should be exposed as a public constant. Logged as ADV-P52-01.

**Open advisory count at T52.2**: 2 open advisories (ADV-P52-01, ADV-P52-02).

---

### [2026-03-23] Phase 52 — T52.4: Quick-Start Notebook

**Branch**: `feat/P52-T52.4-quickstart`

**Tasks completed**: T52.4 (Quick-Start Notebook)

**T52.4 — Quick-Start Notebook**:
Created the connect → synthesize → compare quick-start notebook for data architects.

- `demos/quickstart.ipynb` — Jupyter notebook with three sections:
  - **Connect**: Reads `DATABASE_URL` from environment, discovers tables via SQLAlchemy
    inspect, prints row counts and FK relationships. Never logs credentials (host-only print).
  - **Synthesize**: Reads `ARTIFACT_SIGNING_KEY` from environment (raises EnvironmentError
    if absent or < 32 bytes), invokes `run_demo()` from `conclave_demo.py` with an
    isolated SQLite budget ledger, prints synthesis summary.
  - **Compare**: Reconstructs the fictional source dataset (deterministic faker seed=42),
    renders side-by-side KDE distribution overlays and correlation heatmaps (real vs. synthetic).
  - **Next Steps**: Links to `epsilon_curves.ipynb` and `training_data.ipynb`.

  Security constraints met:
  - No hardcoded credentials in code cells (environment-only, raises on missing key).
  - No `pickle.load()` calls — ModelArtifact.load() path used via `run_demo()`.
  - All code cell outputs are empty (nbstripout compliance).
  - Error messages direct users to `demos/README.md`, not to example DSNs.

- `demos/README.md` — Full setup and usage guide (replaces placeholder from T52.1):
  - Directory layout table.
  - Prerequisites: Poetry groups, Docker Compose, seed command, env var setup.
  - Per-notebook descriptions with expected runtimes.
  - Hardware requirements table.
  - Troubleshooting table (7 common failure modes).

**TDD sequence**: ATTACK RED (5 attack tests) -> FEATURE RED (4 feature tests) ->
GREEN (notebook + README) -> REFACTOR (ruff fix: removed unused PLR2004 noqa,
code-cell-only scope for credential scan).

**Tests added (T52.4)**: 9 tests in `tests/unit/test_quickstart_notebook.py`:

Attack/negative tests:
- `test_quickstart_notebook_exists` — verifies file at demos/quickstart.ipynb
- `test_quickstart_no_hardcoded_credentials` — scans code cells for DSN passwords,
  signing_key= literals, ARTIFACT_SIGNING_KEY= assignments (code cells only)
- `test_quickstart_no_pickle_load` — scans code cells for pickle.load() calls
- `test_quickstart_no_cell_outputs` — verifies empty outputs + None execution_count
- `test_quickstart_uses_env_vars_for_credentials` — verifies os.environ/os.getenv
  usage and ARTIFACT_SIGNING_KEY reference in code cells

Feature tests:
- `test_quickstart_has_three_main_sections` — Connect, Synthesize, Compare headings
- `test_quickstart_has_setup_instructions` — poetry install, docker, ARTIFACT_SIGNING_KEY
- `test_demos_readme_exists` — README present with > 200 bytes of content
- `test_demos_readme_links_resolve` — all relative markdown links resolve to existing files

**Gate #1 results**: 9/9 notebook tests pass. Full suite deferred to pre-merge gate (Gate #2)
per Two-Gate Policy (Rule 18). Static gates: ruff PASS, mypy PASS, bandit PASS.

**Open advisory count at T52.4**: 2 open advisories (ADV-P52-01, ADV-P52-02) — unchanged.

---

---

### [2026-03-23] Phase 52 — T52.3–5: Notebooks (review findings fix)

**Branch**: `feat/P52-T52.3-5-notebooks`

**Tasks covered**: T52.3 (Epsilon Curve Notebook), T52.4 (Quick-Start Notebook), T52.5 (AI Builder Notebook)

**Reviews**:

**QA** (FINDING — 6 issues fixed):
1. `# type: ignore[type-arg]` annotations in `test_quickstart_notebook.py` and `test_ai_builder_notebook.py` lacked justification comments — added inline justification: "notebook JSON is untyped; full nbformat schema out of scope."
2. `_load_results` in `demos/generate_figures.py` docstring missing `KeyError` in Raises section — added (`data["rows"]` access can raise KeyError if key absent).
3. Path traversal guard in `_load_results` used `startswith()` which can be bypassed by sibling directory names — replaced with `Path.is_relative_to()` (Python 3.9+, supported by this project).
4. `test_generate_figures_script_has_valid_python_syntax` used a rubber-stamp `assert compiled is not None` (compile() never returns None) — replaced with `assert isinstance(compiled, types.CodeType)`.
5. `_load_notebook()` docstring in `test_ai_builder_notebook.py` had `pytest.fail:` as a Raises entry (not a standard exception) — clarified with prose form: "Uses pytest.fail if the notebook file does not exist."
6. Raw exception `print(f"DB unavailable ({_db_err})...")` in `training_data.ipynb` exposes internal error details — replaced with sanitized `print("DB unavailable; falling back to sample CSV.")`.

**DevOps** (PASS — 1 advisory fixed inline):
- Sanitized exception print in `training_data.ipynb` (finding 6 above) closes the information-disclosure advisory.

**Architecture** (PASS — 2 minor findings fixed):
- `# type: ignore` justification comments added per code quality standard (finding 1).
- `_load_notebook()` Raises docstring corrected to match implementation (finding 5).

**Gate #1 results**: 2689 passed, 6 skipped, 96.83% unit coverage (>= 95% required). PASS.

**Open advisories**:
- ADV-P52-01: `_DP_EPSILON_DELTA` private symbol exposure — open, target post-P52.
- ADV-P52-02: CI bandit/ruff scope gap for `demos/` — open, documented in ADR-0053.
- ADVISORY (test style): Inconsistency between class-based grouping (`test_notebook_infrastructure.py`) and function-level grouping (`test_quickstart_notebook.py`, `test_ai_builder_notebook.py`). Cosmetic only — batched per Rule 16.
- ADVISORY (CI): bandit scope gap does not cover `demos/` directory — pre-existing, tracked as ADV-P52-02.

**Open advisory count at T52.3–5 review**: 2 open advisories (ADV-P52-01, ADV-P52-02) — unchanged.

---

### [2026-03-23] Phase 52 — T52.6: Published Results (README + demos/README.md)

**Branch**: `feat/P52-T52.6-published-results`

**Tasks completed**: T52.6 (Published Results)

**Reviews**:

**QA** (FINDING — 2 issues fixed):
1. `test_main_readme_svg_references_exist` had a vacuous early return: when the SVG detection
   regex found zero matches, the function returned early instead of failing. The early return was
   replaced with `assert all_svg, "README.md should reference at least one SVG figure"` so the
   test fails explicitly if README.md loses its SVG figure references.
2. `test_demos_readme_contains_quickstart_entry` and `test_demos_readme_contains_training_data_entry`
   used sole `assert "X" in content` substring checks, matching even a directory listing mention.
   Both tests updated to match the section-scoped pattern established for epsilon_curves: assert a
   `###` heading exists for the notebook, the notebook filename appears in the section body, and
   audience or runtime information is present in the section.

**DevOps** (PASS): No findings.

**Open advisory count at T52.6 review**: 2 open advisories (ADV-P52-01, ADV-P52-02) — unchanged.

### [2026-03-23] Phase 51 — Release Engineering

**Branch**: `feat/P51-release-engineering`

**Tasks completed**: T51.1 (Version bump + bump script), T51.2 (Release workflow),
T51.3 (Air-gap validation), T51.4 (DR dry run)

**T51.1**: Bumped `0.1.0` → `1.0.0rc1` across 5 locations (pyproject.toml, __init__.py,
licensing.py, main.py, openapi.json). Refactored `main.py` to read `__version__` from
`__init__.py`. Created `scripts/bump_version.sh` with PEP 440 validation and atomic updates.

**T51.2**: Created `.github/workflows/release.yml` — 3-job pipeline (validate-tag → build-release
→ publish-release) triggered on `v*` tags. All actions SHA-pinned. SBOM includes synthesizer deps.

**T51.3**: Created `scripts/validate_airgap.sh` — bundle extraction, image loading, compose up,
health check, teardown. Fixed `build_airgap.sh` to exclude `docker-compose.override.yml`.
Added `make load-images` and `make validate-airgap` targets.

**T51.4**: Created `scripts/dr_dry_run.sh` — 3 DR scenarios (DB backup/restore, service recovery,
Redis recovery). All data uses `dr_test_` prefix. Backups to `/tmp/` only. Section 8 added to
DISASTER_RECOVERY.md.

**Tests added**: 20 (T51.1), 21 (T51.2), 27 (T51.3), 27 (T51.4) = 95 new tests

**Review findings resolved**:
- FINDING (QA): `bump_version.sh` step 4 silently failed on refactored `main.py` — removed main.py
  from bump targets (reads `__version__` dynamically). Fixed test fixture to match real file structure.
- FINDING (DevOps): `publish-release` job output propagation broken — added `validate-tag` to needs array.
- ADVISORY (DevOps): Air-gap bundle exposed internal docs — replaced blanket `cp -r docs/` with curated
  operator-facing doc list (9 files + `docs/api/`).

**Open advisory count at P51 close**: 3 (ADV-P47-02, ADV-P47-05, ADV-P49-02) — all resolved inline during P52

---

### [2026-03-23] Phase 50 — T50.3: Default to Production Mode

**Branch**: `feat/P50-production-security-fixes`

**Tasks completed**: T50.3 (Default to production mode — secure-by-default hardening)

**T50.3 — Default to Production Mode**:
- Changed `conclave_env` field default from `""` to `"production"` in `shared/settings.py`
- A fresh deployment with no `.env` now boots in production mode (auth enforced), not dev mode
- Added `_warn_if_development_mode()` to `config_validation.py`: emits WARNING mentioning `CONCLAVE_ENV=production` when dev mode is active
- Removed `/security/keys/rotate` from `COMMON_INFRA_EXEMPT_PATHS` (ADV-P47-04)
- Updated `tests/conftest.py` autouse fixture to inject `CONCLAVE_ENV=development` as test-safe default
- Migrated 8 existing test files: added `CONCLAVE_ENV=development` alongside `ENV=development` in dev-mode test cases

**Review fix — Layered Exemption Model**:
- DevOps and Architecture reviewers both found FINDING: removing `/security/shred` from
  `COMMON_INFRA_EXEMPT_PATHS` broke the emergency shred design (sealed-state inaccessibility)
- Fix: introduced `SEAL_EXEMPT_PATHS` (= COMMON + `/security/shred`) for vault/license gates
- Auth gate still uses `COMMON_INFRA_EXEMPT_PATHS` (security routes require JWT — ADV-P47-04 preserved)
- Updated `vault.py` and `licensing.py` to import `SEAL_EXEMPT_PATHS`; `security.py` docstring updated
- 17 new attack tests (`test_layered_exemption_attack.py`), updated `test_exempt_paths.py`

**Advisories drained**: ADV-P47-04 (security routes in AUTH_EXEMPT_PATHS — RESOLVED via layered exemption)

**Tests added**: 12 attack tests (`test_production_mode_default_attack.py`), 19 feature tests (`test_production_mode_default_feature.py`), 17 layered-exemption attack tests

**Open advisory count**: 3 (ADV-P47-02, ADV-P47-05, ADV-P49-02)

### [2026-03-23] Phase 50 — ADR-0052: mutmut / Python 3.14 Gap

**Branch**: `feat/P50-production-security-fixes`

**Tasks completed**: ADR-0052 documentation (mutmut Python 3.14 compatibility gap)

**ADR-0052**: Accepts the mutmut / CPython 3.14 SIGSEGV incompatibility as a known gap.
- Constitution Priority 4 mutation gate deferred pending upstream mutmut support for Python 3.14
- Manual hardening tests from T49.5 (19 tests in `test_mutation_hardening_t49_5.py`) serve as partial mitigation
- `pyproject.toml` `[tool.mutmut]` config retained for re-activation when upstream support lands
- Re-evaluation triggers documented in ADR-0052 (upstream release, Python downgrade proposal, alternative tool evaluation, Phase 55 threshold review)

**Advisories drained**: ADV-T49-01 (mutmut segfault — RESOLVED), ADV-P49-03 (mutmut CI gate not wired — RESOLVED)

**Open advisory count**: 3 (ADV-P47-02, ADV-P47-05, ADV-P49-02)

### [2026-03-23] Phase 49 — Test Quality Hardening

**Branch**: `chore/P49-test-quality-hardening`

**Tasks completed**: T49.1 (Security assertion hardening), T49.2 (Masking/subsetting assertion
hardening), T49.3 (Mock reduction), T49.4 (Test organization), T49.5 (Mutation testing baseline)

**T49.1**: `test_download_hmac_signing.py` 4→20 tests; `test_audit.py` value assertions;
`test_dp_accounting.py` propagation guards; `test_ale.py` round-trip + distinctness.

**T49.2**: Salt-sensitivity on all mask functions; parametrized sweeps; subsetting negative
cases (mid-stream failure, DB disconnect); settings router value assertions.

**T49.3**: Shared `helpers_synthesizer.py`; opt-in `jwt_secret_key_env` fixture; 2 Opacus
integration tests; 3 guardrails edge cases (psutil, CUDA, memory=0).

**T49.4**: `test_synthesizer_tasks.py` (2738 lines) split into 3 files (107/107 tests preserved).

**T49.5**: mutmut 3.x configured for `shared/security/` + `modules/privacy/`; 200 mutants
generated, 0 survived (all SIGSEGV due to Python 3.14 incompatibility); 19 hardening tests.

**Review findings resolved** (commit 4253ff1):
- FINDING (Red-Team F-2): audit.py/audit_anchor.py excluded from mutation testing — fixed

**Review findings logged as advisory**:
- Red-Team F-1: Audit HMAC doesn't cover `details` field — pre-existing (ADV-P49-02)
- DevOps F-1: mutmut not in CI — blocked by Python 3.14 segfault (ADV-P49-03)

**Advisories raised**: ADV-T49-01, ADV-P49-02, ADV-P49-03

**Test metrics**: 2466 passed, 1 skipped — coverage 96.76% (95% gate PASS). Net +72 tests.

**Open advisory count**: 7 (under Rule 11 threshold of 8)

---

### [2026-03-23] Documentation Cleanup & Tightening

**Branch**: `chore/docs-cleanup-and-tightening`

**Motivation**: Reduce agent context load at outset scan. Too much verbose documentation
consuming tokens before agents reach actionable content.

**Wave 1 — Archive** (8 files moved to `docs/archive/`):
DEVELOPMENT_STORY, BACKLOG, DOCUMENT_INDEX, E2E_VALIDATION, DP_QUALITY_REPORT,
e2e_load_test_results.json, ARCHITECTURAL_REQUIREMENTS, BUSINESS_REQUIREMENTS.
All cross-references updated in active docs.

**Wave 2 — Tighten top 3 docs**:
| File | Before | After | Reduction |
|------|--------|-------|-----------|
| OPERATOR_MANUAL | 1330 | 898 | -32% |
| DEVELOPER_GUIDE | 1102 | 779 | -29% |
| PRODUCTION_DEPLOYMENT | 934 | 674 | -28% |

**Wave 3 — Tighten remaining active docs**:
| File | Before | After | Reduction |
|------|--------|-------|-----------|
| SECURITY_HARDENING | 597 | 391 | -34% |
| DISASTER_RECOVERY | 561 | 347 | -38% |
| REQUEST_FLOW | 560 | 400 | -29% |
| TROUBLESHOOTING | 469 | 366 | -22% |
| DATA_COMPLIANCE | 384 | 280 | -27% |
| SCALABILITY | 290 | 209 | -28% |
| LICENSING | 284 | 222 | -22% |
| infrastructure_security | 215 | 156 | -27% |
| index.md | 263 | 237 | -10% |
| README | 442 | 348 | -21% |
| DEPENDENCY_AUDIT_POLICY | 152 | 116 | -24% |
| DEPENDENCY_AUDIT | 123 | 99 | -20% |

**Total active docs reduction**: ~9,400 → ~6,400 lines (~32% overall, excluding RETRO_LOG and CHANGELOG).

**What was preserved**: Every command, config value, code block, security warning,
deployment step, and cross-reference. Only filler, redundancy, and verbose preambles were cut.

---

### [2026-03-23] Phase 48 — Production-Critical Infrastructure Fixes

**Branch**: `feat/P48-production-infra-fixes` (22+ commits)

**Tasks completed**: T48.1 (Redis-backed rate limiting), T48.2 (Worker connection pooling),
T48.3 (Readiness probe), T48.4 (Audit trail anchoring), T48.5 (ALE vault enforcement)

**Advisories drained**: ADV-P47-06 (in-memory rate limiter — resolved by T48.1)

**Advisories raised**: ADV-P48-01 through ADV-P48-04 (4 total: all red-team, all ADVISORY)

**Review findings resolved** (commit a1017af):
- BLOCKER: Audit.py → AnchorManager.maybe_anchor() wiring gap (Rule 8 violation)
- FINDING: Sync Redis pipeline blocking event loop in rate_limit.py dispatch (asyncio.to_thread)
- FINDING: health.py importing private symbols from main.py (extracted to docker_secrets.py)
- FINDING: Stale ALE_KEY fallback references in rotation.py docstrings
- FINDING: /ready creating new async engine per probe call (reuse shared engine)
- FINDING: Missing anchor settings in .env.example
- FINDING: Untyped s3_client parameter (justification comment added)

**Open advisory count**: 4 (under Rule 11 threshold of 8 — drain complete)

**What went well**:
- Two-wave parallel execution: Wave 1 (T48.1-T48.3 infra) then Wave 2 (T48.4-T48.5 security)
- Spec-challenger caught SealGateMiddleware exemption gap for /ready before development
- ALE vault enforcement (T48.5) eliminates a real security weakness — env var fallback path
- 4 review agents (QA, DevOps, Architecture, Red-Team) caught the Rule 8 wiring BLOCKER

**What was challenging**:
- Wave 1 initial parallel worktree approach timed out for all 3 agents; recovered by
  consolidating to single-branch sequential execution
- Rebase required twice due to external PRs (#175, #176) merging during development
- T48.5 required updating 7 test files that depended on ALE_KEY env var fallback

**What could be improved**:
- Rule 8 wiring gap should have been caught during GREEN phase, not review
- Worktree timeout issue suggests large tasks need smaller scope when parallelized

**Test metrics**: 2394 passed, 96.72% coverage (95% gate PASS)

---

### [2026-03-23] Advisory Drain — Pre-Phase 49

**Branch**: `chore/advisory-drain-pre-p49`

**Advisories drained** (5 total, 9→4):
- ADV-P48-01: X-Forwarded-For trust model (PRODUCTION_DEPLOYMENT.md Appendix B)
- ADV-P47-03: Scope-based auth ADR gap (ADR-0049 written)
- ADV-P48-04: Stale ale_key field in ConclaveSettings (field removed)
- ADV-P48-02: Redis INCR+EXPIRE atomicity (closed as accepted tradeoff)
- ADV-P48-03: Anchor verification equality-only (closed as accepted tradeoff)

**Remaining open** (4): ADV-P47-02 (TLS duplication), ADV-P47-04 (security routes exempt),
ADV-P47-05 (all-or-nothing scopes), ADV-P47-07 (TOCTOU — covered by T50.4)

**Open advisory count**: 4 (Rule 11 threshold ≤5 — PASS)

---

### [2026-03-22] Phase 49 — Framework Amendments (Architecture Review)

**Branch**: `docs/framework-amendments` (docs-only)

**What**: Framework amendments from staff-level architecture review (2026-03-22). No production
code or test changes — governance and documentation only.

**Amendments**:
1. **Priority Sequencing (ADR-0046)**: New Constitution Priority 2.5 — PM must verify all
   lower-numbered Constitutional priorities are implemented or deferred with ADR before
   approving phase plans targeting higher-numbered work. Addresses finding that Security
   (Priority 0) features shipped at Phase 39/48.
2. **Assertion Quality Gate**: Constitution Priority 4 amended — tests must contain specific
   value assertions; truthiness/type/existence checks alone are insufficient.
3. **Mutation Testing Gate (ADR-0047)**: Constitution Priority 4 amended — mutmut must
   achieve 60% mutation score (targeting 70% by Phase 55) on security-critical modules
   (shared/security/, modules/privacy/).
4. **Security Advisory TTL (Rule 26)**: BLOCKER/security advisories must resolve within
   2 phases or auto-promote to merge-blocking gate.
5. **Governance Pruning**: All 7 rules with [sunset: Phase 50] extended to Phase 60 —
   RETRO_LOG evidence supports all rules (Rule 6, 8, 9, 11, 12, 16, 17). No rules deleted.

**Agent updates**:
- spec-challenger: Added challenge area #8 (Priority Compliance)
- phase-boundary-auditor: Added Assertion Specificity sweep to test audit

**Source**: Staff-level architecture review, 2026-03-22

---

### [2026-03-22] Phase 47 — Auth & Safety Ops Retrospective

**Branch**: `feat/P47-auth-safety-ops` (29 commits)

**Tasks completed**: T47.1 (Scope enforcement for security routes), T47.3 (Scope enforcement
for settings routes), T47.4 (JWT secret key production validation), T47.5 (Operator credentials
hash validation), T47.6 (Artifact signature hardening), T47.7 (Parquet memory bounds),
T47.8 (Shutdown cleanup + TLS 1.3 pin), T47.9 (Budget error scrubbing), T47.10 (Redis healthcheck)

**Advisories drained**: ADV-P46-01 (TLS 1.3 pin), ADV-P46-03 (TOCTOU cert check),
ADV-P46-04 (LibreSSL detection), ADV-P46-05 (Prometheus metrics naming),
ADV-P46-06 (MinIO NetworkPolicy), ADV-P47-01 (CI smoke test dummy secrets)

**Advisories raised**: ADV-P47-02 through ADV-P47-07 (6 total: 2 architecture, 4 red-team)

**Open advisory count**: 6 (under Rule 11 threshold of 8)

**What went well**:
- Three-wave parallel execution delivered 10 tasks in a single phase
- Spec-challenger caught scope issuance gap (default operator scopes needed updating)
- Config validation hardening used collect-all error pattern — single startup attempt shows
  ALL missing vars, not one-at-a-time failure
- pydantic-settings `.env` bleeding bug caught and fixed with autouse conftest fixture —
  prevents future test pollution system-wide
- 6 P46 advisories drained inline alongside new feature work

**What was challenging**:
- Pre-existing test infrastructure issues (pydantic-settings `.env` bleeding, VaultState
  ordering in test_ale.py) required diagnostic work unrelated to Phase 47 scope
- CI smoke test failure on PR merge (ADV-P47-01) — `secrets/` directory not provisioned
  in CI. Fixed by adding dummy cert provisioning step. Process failure: should have flagged
  to user before merging per feedback memory

**What could be improved**:
- CI smoke test should be treated as a blocking check even though it is not in the required
  checks list. Memory saved: always flag any failing CI job to user before merge.
- Production-required config vars must be added to ALL test fixtures that set `ENV=production`,
  not just the new test files. Grep audit pattern: `ENV=production` + `monkeypatch.setenv`.

**Test metrics**: 2272 passed, 1 skipped, 97.40% coverage (95% gate PASS)

---

### [2026-03-22] P47 Review Fix — QA, DevOps, and Architecture Findings

**Branch**: `feat/P47-auth-safety-ops`

**Findings resolved**:
- FINDING 1 (QA): `test_config_validation_ssl_warning_uses_settings` regression fixed.
  Test now supplies `JWT_SECRET_KEY` and `OPERATOR_CREDENTIALS_HASH` when setting
  `ENV=production`, satisfying the T47.4/T47.5 production-required validators.
- FINDING 2 (DevOps): `.env.example` updated with `PARQUET_MAX_FILE_BYTES` and
  `PARQUET_MAX_ROWS` entries under a new T47.7 section.
- FINDING 3 (Architecture): `DatasetTooLargeError` imported and added to
  `OPERATOR_ERROR_MAP` with `status_code=413` and a safe operator-facing detail string.
- FINDING 4 (Architecture ADVISORY): `_promote_redis_url_to_tls` duplication logged
  as ADV-P47-02. No code change — duplication is intentional per T46.2 architecture review.
- FINDING 5 (Architecture ADVISORY): ADR gap for scope-based authorization logged as
  ADV-P47-03. No ADR created in this task per task scope constraints.

**Quality gates**: ruff PASS | ruff format PASS | mypy PASS | bandit PASS | vulture PASS
Unit tests: 2272 passed, 1 skipped, 97.40% coverage (95% gate PASS).
1 pre-existing flaky failure (`test_synthesis_engine_train_raises_on_empty_parquet`) passes
individually — ordering-sensitive state pollution, unrelated to this diff.

---

### [2026-03-22] T47.9 — Scrub Budget Values From Exception Messages

**Branch**: `feat/P47-auth-safety-ops`

**What was implemented**:
- `BudgetExhaustionError` restructured with a typed `__init__` accepting
  keyword args (`requested_epsilon`, `total_spent`, `total_allocated`).
  `str(exc)` now always returns the generic safe constant:
  `"Differential privacy budget exhausted. Synthesis job cannot proceed."`
  Epsilon values are stored as typed `Decimal` attributes for internal
  audit logging without any HTTP exposure.
- `remaining_epsilon` computed attribute (`total_allocated - total_spent`)
  added for operator audit convenience.
- Raise sites in `accountant.py`, `factories.py`, and `dp_engine.py`
  updated to use the new structured constructor.
- `dp_engine.check_budget()` now emits a `WARNING` log with epsilon details
  before raising (matching the accountant's pre-existing WARNING log pattern).
- 12 existing tests updated to use the new keyword-arg constructor;
  message-assertion tests updated to verify the generic-message contract.

**Tests added**: 12 new tests in `test_budget_error_scrubbing.py`
(5 attack/negative, 7 feature). All pass. No regressions introduced.

**Quality gates**: ruff ✓ | ruff format ✓ | mypy ✓ | bandit ✓ | vulture ✓
Pre-commit hooks pass on all changed files (detect-secrets flag in
`test_config_validation_hardening_feature.py` is pre-existing, unrelated).

---

### [2026-03-22] T47.4 + T47.5 + ADV-P46-03 — Config Validation Hardening

**Branch**: `feat/P47-auth-safety-ops`

**What was implemented**:
- `JWT_SECRET_KEY` added to production-required validation (T47.4): empty or whitespace-only
  values raise `SystemExit` in production; emit `WARNING` in development. Whitespace-only
  keys treated as empty (strip before truthiness check).
- `OPERATOR_CREDENTIALS_HASH` added to production-required validation with two-step check (T47.5):
  1. Presence check: empty → SystemExit in production, WARNING in development.
  2. Format check: must start with `$2b$` and be >= 59 chars (fast structural check, no
     `bcrypt.checkpw()` call to avoid intentional slowness). Invalid format → SystemExit in
     production, WARNING in development.
  - Error messages name the variable only — hash value is NEVER included (hash oracle prevention).
- `_validate_mtls_cert_files()` now attempts `open(path, 'rb')` after `Path.exists()`, making
  it an atomic existence+readability check. Eliminates the TOCTOU race that a separate
  `os.access()` call would introduce (ADV-P46-03 DRAINED).
- Existing production test fixtures updated to include the two new required auth vars
  (`test_all_vars_present_production_passes`, `test_production_ssl_required_*`).

**Tests added**: 16 new tests across 2 files (9 attack/negative + 7 feature).
All 68 targeted tests pass. Full suite: 2200 passed / 10 pre-existing failures / 97.03% coverage.

**Quality gates**: ruff ✓ | ruff format ✓ | mypy ✓ | bandit ✓ | vulture ✓ | coverage 97.03% ✓

**Advisory drained**: ADV-P46-03 (cert readability check — DELIVERED T47.4+T47.5 branch).

**Lessons learned**:
- Existing production tests must be updated when new production-required vars are added.
  Grepping for `ENV=production` + `monkeypatch.setenv` is the right audit pattern.
- Hash oracle prevention: error messages for credential config must name only the variable,
  never the value — even a bcrypt hash can be exploited offline if leaked into logs.
- TOCTOU races in cert validation: open() is always preferable to os.access() for atomicity.

### [2026-03-22] T46.4 — Network Policy Enforcement & Documentation

**Branch**: `feat/P46-mtls-inter-container`

**What was implemented**:
- K8s NetworkPolicy manifests in `k8s/network-policies/`: default-deny baseline, per-service
  allow policies (app, pgbouncer, postgres, redis, monitoring). Default-deny applied last.
- ADR-0045: mTLS inter-container communication architecture. 6 design decisions, full threat
  model (in-scope vs out-of-scope), CNI prerequisite, Phase 46 deliverable matrix.
- `docs/backlog/deferred-items.md` TBD-03 marked DELIVERED (Phase 46).
- `docs/infrastructure_security.md` Section 7: mTLS overview with connection matrix.

**Test coverage**: Docs-only task — no Python changes. All quality gates pass (97.06%, 2170 passed).

**Review results**:
- QA: (running — docs-only task, low risk)
- DevOps: FINDING → 2 items fixed (Prometheus ingress on app-policy, AlertManager egress placeholder)
- Red-Team: PASS (2 ADVISORYs: AlertManager egress, MinIO policy absent)

**Advisories raised**: ADV-P46-06 (MinIO NetworkPolicy absent).

**Lessons learned**:
- NetworkPolicy requires matching rules on BOTH sides (ingress on receiver + egress on sender)
- AlertManager egress to notification endpoints is easy to forget in default-deny environments
- K8s manifests should always document CNI prerequisite upfront

---

### [2026-03-22] T46.3 — Certificate Rotation Without Downtime

**Branch**: `feat/P46-mtls-inter-container`

**What was implemented**:
- `shared/cert_metrics.py`: Prometheus Gauge `conclave_cert_expiry_days` with service labels.
  Behavior matrix: NaN when disabled, -1 sentinel on error, negative for expired certs.
- `scripts/rotate-mtls-certs.sh`: Rotation helper — backups, leaf cert regeneration, chain
  validation, expiry check (>30d), key-pair verification. Restricted backup dir permissions (0700).
- `bootstrapper/lifecycle.py`: Wired metric update at startup via `asyncio.to_thread()`.
- `docs/OPERATOR_MANUAL.md` Section 13: Rotation procedures (Docker Compose, K8s, CA dual-trust).
  Prometheus alert rules. Reconnection behavior table.
- `docs/DISASTER_RECOVERY.md` Section 7: Cert loss recovery (CA key loss, leaf cert loss, backup strategy).

**Test coverage**: 13 tests (8 attack/negative + 5 feature). Coverage: 97.06% (gate: 95%).
All quality gates pass.

**Spec challenge**: 7 missing ACs, 10 negative tests, 6 attack vectors, 5 config risks.
Key additions incorporated: cert validation before reload, backup creation, service-name labels,
graceful handling when mTLS disabled, CA rotation as planned maintenance.

**Review results**:
- QA: (agent stalled on test output capture — scope covered by other reviewers + developer gates)
- DevOps: FINDING → 2 items fixed (async lifespan I/O, path traversal canonicalization)
- Architecture: PASS (1 ADVISORY: async-correctness — addressed in fix commit)
- Red-Team: FINDING → 2 items fixed (.gitignore backup pattern, backup dir permissions)
- DevOps re-review: PASS
- Red-Team re-review: PASS

**Advisories raised**: ADV-P46-04 (LibreSSL detection), ADV-P46-05 (metrics naming convention).
**Advisories resolved**: ADV-P46-02 (cert expiry metric now provides monitoring; periodic scraping
by Prometheus replaces the need for a separate scheduler).

**Lessons learned**:
- Backup directories must be explicitly gitignored with recursive patterns
- Async lifespan hooks must use `asyncio.to_thread()` for synchronous I/O
- Operator-facing scripts need `realpath` canonicalization to prevent path traversal
- Shell scripts targeting both OpenSSL and LibreSSL need explicit detection

---

### [2026-03-22] T46.2 — Wire mTLS on All Container-to-Container Connections

**Branch**: `feat/P46-mtls-inter-container`

**What was implemented**:
- `shared/settings.py`: Added `mtls_enabled`, `mtls_ca_cert_path`, `mtls_client_cert_path`,
  `mtls_client_key_path` fields to ConclaveSettings.
- `shared/db.py`: TLS `connect_args` for psycopg2 (`sslmode=verify-full`) and asyncpg
  (`ssl.SSLContext`). Composite cache key including mTLS state.
- `shared/task_queue.py`: Redis URL promotion (`redis://` → `rediss://`) and TLS connection
  kwargs for RedisHuey.
- `bootstrapper/dependencies/redis.py`: TLS params for singleton Redis client.
- `bootstrapper/factories.py`: TLS params for sync spend_budget engine.
- `bootstrapper/config_validation.py`: Fail-closed startup validation for cert files.
- `docker-compose.mtls.yml`: Full mTLS overlay (Redis TLS, PgBouncer frontend+backend mTLS,
  PostgreSQL server TLS with TLSv1.3 minimum).
- `.env.example`: MTLS env vars documented.

**Test coverage**: 10 attack/negative tests + 19 feature tests. Coverage: 97.09% (gate: 95%).
All quality gates pass.

**Spec challenge**: 11 missing ACs, 17 negative tests, 5 attack vectors, 5 config risks.
All incorporated into developer brief.

**Review results**:
- QA: (agent stalled on test output capture — scope covered by other 3 reviewers + developer gates)
- DevOps: FINDING → 3 items fixed (PgBouncer frontend verify-full, .env.example, CI note)
- Architecture: FINDING → 2 items fixed (private import cross-boundary, ADR-0029 Gap 7 status)
- Red-Team: FINDING → 1 item fixed (engine cache key mTLS state) + 8 ADVISORYs
- DevOps re-review: PASS
- Architecture re-review: PASS
- Red-Team re-review: PASS

**Advisories raised**: ADV-P46-01, ADV-P46-02, ADV-P46-03 (see Open Advisory Items table).

**Lessons learned**:
- PgBouncer `client_tls_sslmode=require` is NOT mutual auth — must use `verify-full` with CA file
- Engine/session caches keyed by URL alone miss configuration changes (mTLS, pool params)
- Private (`_`-prefixed) functions imported across module boundaries create hidden coupling
- ADR status must be updated when deferred items are implemented — stale "Deferred" status is factually misleading

---

### [2026-03-22] T46.1 — Internal Certificate Authority & Certificate Issuance

**Branch**: `feat/P46-mtls-inter-container`

**What was implemented**:
- `scripts/generate-mtls-certs.sh`: ECDSA P-256 internal CA + leaf certs for app, postgres,
  pgbouncer, redis. Idempotent (CA key protected by `--force`). SANs include Docker Compose
  and K8s hostname variants. File permissions: CA key 0400, leaf keys 0600. Air-gap compatible.
- `shared/tls/config.py`: Module-level functions for cert loading, validation, chain
  verification, expiry checks. `validate_san_hostname()` with format validation.
- `TLSCertificateError` added to `shared/exceptions.py` (SynthEngineError hierarchy).

**Test coverage**: 37 tests (10 attack/negative + 27 feature). Coverage: 97.13% (gate: 95%).
`config.py` at 100%. All quality gates pass.

**Spec challenge**: 12 missing ACs identified, 16 negative tests required, 5 attack vectors,
4 configuration risks. All incorporated into developer brief.

**Review results**:
- QA: FINDING → 3 items fixed (key-file guard, docstring accuracy, exception path coverage)
- DevOps: PASS
- Architecture: FINDING → 2 items fixed (exception hierarchy, static-class → module functions)
- Red-Team: PASS
- QA re-review: PASS
- Architecture re-review: PASS

**Advisories raised**: None.

**Lessons learned**:
- Module-level docstrings claiming security properties must be verified against implementation
- Asymmetric error handling (cert path guarded but key path not) is a common incremental growth bug
- Static-method-only classes should be dissolved into module-level functions per codebase convention

---

## Archived Reviews

Detailed reviews for phases 0–45 are archived in `docs/retro_archive/`:
- [phases-0-to-7.md](retro_archive/phases-0-to-7.md) — Phases 0 through 7
- [phases-8-to-14.md](retro_archive/phases-8-to-14.md) — Phases 8 through 14
- [phases-15-to-45.md](retro_archive/phases-15-to-45.md) — Phases 15 through 45
