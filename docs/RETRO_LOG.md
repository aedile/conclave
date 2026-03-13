# Conclave Engine — Retrospective Log

Living ledger of review retrospective notes and open advisory items.
Updated after each task's review phase completes.

---

## Open Advisory Items

Advisory findings without a resolved target task are tracked here.
Drain (delete) rows when their target task is completed.

| ID | Source | Target Task | Advisory |
|----|--------|-------------|----------|
| ADV-006 | Arch R2 | T2.1+ completed — seed work pending | `docs/ARCHITECTURAL_REQUIREMENTS.md` is referenced in `scripts/seed_chroma.py` (SEEDING_MANIFEST) and `docs/adr/ADR-0002` but does not exist in the repo. If absent at runtime, `seed_chroma.py` will `sys.exit(1)` when trying to seed the ADRs collection. Create this file (or update the manifest path) before Phase 2 seeding work begins. |
| ADV-007 | DevOps R1/R3 | Standalone CI hardening task | GitHub Actions in `ci.yml` are pinned to mutable version tags (`@v4`, `@v2`) not commit SHAs. Third-party actions (`gitleaks-action@v2`, `snok/install-poetry`) carry supply-chain risk. SHA-pin all actions in a dedicated CI hardening pass. |
| ADV-008 | QA/DevOps P0.8.1 | Before Task 4.2 (SDV integration) | `_process_chunk()` in `spike_ml_memory.py` uses `except ValueError: pass` — silent swallow must be replaced with `WARNING`-level logging before any synthesizer code is promoted to `src/synth_engine/modules/synthesizer/`. Also: numpy fast path uses unseeded `np.random.normal` (global PRNG state) — breaks determinism; must seed `np.random.default_rng` from same seed as stdlib PRNG before Phase 4 promotion. |
| ADV-009 | QA P0.8.1 | Before Phase 4 | `spikes/` directory is outside bandit and ruff scan targets. As spike code accumulates and patterns are promoted to `src/`, this creates a scan blind spot. Add `spikes/` to bandit targets in `pyproject.toml` or add a `.bandit` marker documenting the intentional exclusion. Also add `# noqa: S311` alongside existing `# nosec B311` at `spike_ml_memory.py` lines 379 and 522. |
| ADV-010 | QA P0.8.2 | Before Phase 3 | `# nosec B311`/`# nosec B608` suppresses bandit only — ruff needs separate `# noqa: S311`/`# noqa: S608` annotations. Four S608 violations exist in `spikes/spike_topological_subset.py`. Fix: add `"spikes/**" = ["S311", "S608"]` to `[tool.ruff.lint.per-file-ignores]` in `pyproject.toml`. This pattern will recur when SQL-adjacent code lands in Phase 3 `src/ingestion/` — apply dual annotations there from the first commit. |
| ADV-011 | QA P0.8.2 | Before Phase 4 (masking module) | `FeistelFPE` in `spike_fpe_luhn.py` has unguarded edge cases: `rounds=0` is an identity transformation (no encryption); `luhn_check("")` and `_luhn_check_digit("")` return `False`/`"0"` silently. Write `tests/unit/test_fpe_luhn.py` (TDD RED) against spike code before promoting to `src/synth_engine/modules/masking/`. Also document spike-to-production promotion checklist in `AUTONOMOUS_DEVELOPMENT_PROMPT.md` before Phase 4. |
| ADV-012 | QA P0.8.3 | Before Phase 3 (ingestion module) | `SubsetQueryGenerator._resolve_reachable()` uses "any-parent OR" semantics to mark a table reachable — correct for downstream-pull subsetting but must be explicitly decided in an ADR before Phase 3 implementation to prevent correctness regressions. Also: `_infer_pk_column()` checks `pk==1` only (incorrect for composite-PK tables). Both must be addressed in the Phase 3 ADR for ingestion subsetting. |
| ADV-013 | DevOps P0.8.3 | Before Phase 3 (ingestion module) | When `SubsetQueryGenerator` is promoted to `src/synth_engine/modules/ingestion/`, `seed_table` crosses a trust boundary. Require allowlist validation against `SchemaInspector.get_tables()` before any f-string SQL construction. Document `spikes/` CI carve-out (no mypy/ruff/bandit enforcement) explicitly in ADR or README so future reviewers do not mistake the absence of enforcement for an oversight. |
| ADV-014 | DevOps P1-T1.3–1.7 | Before Phase 2 ships | Dockerfile FROM lines for `node:20-alpine`, `python:3.14-slim`, and `redis:7-alpine` use floating minor-version tags. A silent tag update can introduce new packages or CVEs without triggering a dependency review. Pin all FROM lines to SHA-256 digests (e.g. `python:3.14-slim@sha256:<digest>`) before any production deployment. |
| ADV-015 | DevOps P1-T1.3–1.7 | Standalone CI hardening task | No Trivy image-scan job in `ci.yml`. The Dockerfile comment notes a manual trivy scan but this is unenforced. Add `aquasecurity/trivy-action` to CI with `exit-code: 1` on CRITICAL/HIGH CVEs — makes the image-CVE gate as automatic as bandit and pip-audit. Bundle with ADV-007 (SHA-pin GitHub Actions) into a single CI hardening pass. |
| ADV-016 | UI/UX P1-T1.3–1.7 | Before Phase 5 dashboard task | Three accessibility pre-conditions from the Docker topology: (1) CSP headers for React/Vite SPA must be established in FastAPI middleware before frontend build starts — restrictive `script-src 'self'` will block inline scripts used by accessibility polyfills; (2) any Jaeger iframe embed needs `<iframe title="...">` and documented third-party WCAG scope exclusion; (3) MinIO console must be treated as internal developer tool only — never surfaced to end users. |

---

## Task Reviews

---

### [2026-03-13] P2-T2.3 — Zero-Trust JWT Auth (client-binding, RBAC scopes, PyJWT migration)

**QA** (Round 1 — FINDING, all resolved):
Two blockers caught. (1) `request.client is None` unguarded in `extract_client_identifier()` — AttributeError 500 on Unix socket / minimal ASGI; fixed with explicit None guard raising `TokenVerificationError(status_code=400)`. (2) `scopes.py` ValueError handler caught silently with no logging — audit gap in zero-trust boundary; fixed with `logger.warning("Unrecognised scope string: %r — skipping", raw)`. All 100 tests pass, 100% coverage. Retrospective: `request.client` and other optional Starlette attributes should have a dedicated None-input test as a standing convention; security modules must log every unexpected token value.

**UI/UX** (Round 1 — SKIP):
No templates, routes, forms, or interactive elements. Forward: 401/403 responses need human-readable, actionable error messages properly associated to context when JWT/RBAC dependencies are wired into FastAPI routes and templates.

**DevOps** (Round 1 — FINDING, all resolved):
(1) `bound_client_hash != expected_hash` used Python `!=` (not constant-time) — timing side-channel; fixed with `hmac.compare_digest()`. (2) `X-Client-Cert-SAN` header taken verbatim with no proxy-stripping documentation — critical security assumption; documented in ADR-0008 with CRITICAL note that reverse proxy must strip incoming header. (3) `X-Forwarded-For` trust boundary undocumented — added to ADR-0008 threat model. (4) `.env.example` missing `JWT_SECRET_KEY` — added with generation instructions. pip-audit clean; bandit 0 issues. Retrospective: proxy-forwarded identity headers require an ADR entry documenting stripping requirement for every new pattern — a runtime `TRUSTED_PROXY_CIDRS` guard should be considered in Phase 5.

**Architecture** (Round 1 — FINDING, all resolved):
Two blockers. (1) `jwt.py` imported FastAPI (`Request`, `HTTPException`, `Depends`) — framework imports forbidden in `shared/`; resolved by extracting `get_current_user()` Depends factory to `bootstrapper/dependencies/auth.py`; `shared/auth/jwt.py` now framework-agnostic with `TokenVerificationError(Exception)`. (2) `python-jose[cryptography]` runtime dep without ADR — ADR-0007 written (subsequently updated to document PyJWT migration after CVE-2024-23342 discovered in ecdsa transitive dep); zero-trust token-binding pattern — ADR-0008 written. Import-linter reverse guard (shared must not import from modules or bootstrapper) added to `pyproject.toml`. CI blocker: CVE-2024-23342 in `ecdsa` (via python-jose) — replaced with `PyJWT[cryptography]>=2.10.0`; ADR-0007 updated. Retrospective: `shared/` must remain framework-agnostic without exception; ADR-per-dependency norm is load-bearing governance.

---

### [2026-03-13] P2-T2.2 — Database Layer (PostgreSQL, PgBouncer, SQLModel ORM, ALE)

**QA** (Round 1 — FINDING, all resolved):
(1) `dialect` parameter in `EncryptedString.process_bind_param` and `process_result_value` flagged by vulture at 80% confidence (dead code) — renamed to `_dialect`. (2) Three ALE test gaps: empty string roundtrip, malformed `ALE_KEY` raises `ValueError`, corrupted ciphertext raises `InvalidToken` — all three tests added; `ale.py` now at 100% coverage. (3) `malformed ALE_KEY` exception contract undocumented — docstring updated with `ValueError` and `InvalidToken` contracts. 39 tests, 97% total coverage. Retrospective: encryption TypeDecorators have three distinct failure modes (happy path, malformed key, corrupted ciphertext) that are easy to miss; these three test categories should be standing fixtures in the test template.

**UI/UX** (Round 1 — SKIP):
No templates, routes, forms, or interactive elements. Forward: encrypted fields (Fernet ALE) are opaque to DB queries — future UI tasks needing to display or filter PII fields must design around this constraint (client-side decryption or pre-tokenized search indexes).

**DevOps** (Round 1 — FINDING, all resolved):
(1) PgBouncer had no auth configuration — connections succeeded but were completely unauthenticated (blocker); fixed with `PGBOUNCER_AUTH_TYPE=md5`, `PGBOUNCER_AUTH_FILE`, and `pgbouncer/userlist.txt`. (2) `.env.example` missing `ALE_KEY`, `DATABASE_URL`, `PGBOUNCER_URL` — all added. Advisory: `postgres:16-alpine` and `edoburu/pgbouncer:1.23.1` not SHA-pinned (development acceptable; production requires digest pin). Advisory: Fernet key rotation requires full-table re-encryption; no tooling yet (deferred to Phase 6). CI blocker: CVE-2026-26007 in `cryptography<46.0.5` — pinned to `>=46.0.5,<47.0.0`. Retrospective: every new docker-compose service needs explicit authentication configured as an acceptance criterion.

**Architecture** (Round 1 — FINDING, all resolved):
(1) ALE pattern (Fernet TypeDecorator) required ADR before merge — ADR-0006 written documenting GDPR/HIPAA/CCPA alignment, key rotation constraints, search limitations, lru_cache design (blocker). File placement correct: `shared/security/ale.py` and `shared/db.py` both cross-cutting. Dependency direction clean: no module-level imports. Advisory: `BaseModel(SQLModel)` has no runtime guard against direct instantiation; deferred to first concrete model addition. Retrospective: ADR-per-dependency norm forces explicit documentation of data loss risk and search limitations — architectural constraints future developers need before designing features.

---

### [2026-03-13] P2-T2.1 — Module Bootstrapper (FastAPI, OTEL, Idempotency, Orphan Reaper)

**QA** (Round 1 — FINDING, all resolved):
Five findings. (1) `exists()+setex()` TOCTOU race in idempotency middleware — replaced with atomic `SET NX EX` returning 409 on duplicate (blocker). (2) `RedisError` uncaught — middleware now logs warning and passes through; app stays available when Redis is down (blocker). (3) Idempotency key consumed on downstream error — best-effort `delete(key)` added so caller can retry. (4) `fail_task()` exception in reaper loop caused full loop abort — wrapped in `try/except`; logs ERROR and continues. (5) `telemetry.py` docstrings inaccurately described `InMemorySpanExporter` — updated (dev/test only). 56 tests, 99.30% coverage. Retrospective: any future middleware touching external I/O must use async clients; Redis `SET NX EX` is the canonical pattern for distributed idempotency locks.

**UI/UX** (Round 1 — SKIP):
No templates, routes, forms, or interactive elements. The GET `/health` endpoint returns JSON — no accessibility concerns. Forward: HTTP 409 responses from idempotency middleware should be handled gracefully in the React SPA (retry with exponential backoff; display status accessibly).

**DevOps** (Round 1 — FINDING, all resolved):
(1) `main.py` at `src/synth_engine/main.py` — Dockerfile CMD would reference non-existent module path (blocker); moved to `bootstrapper/main.py`. (2) `IdempotencyMiddleware` used synchronous Redis client in async context — event loop stalled silently under load (blocker); now uses `redis.asyncio`. (3) 128-char idempotency key cap added (HTTP 400). (4) `_redact_url()` helper strips userinfo from OTLP endpoint before logging. Advisory: `.env.example` missing `OTEL_EXPORTER_OTLP_ENDPOINT` and `REDIS_URL` (deferred). `pre-commit-config.yaml` mypy `additional_dependencies` updated. Retrospective: synchronous Redis in async middleware is a footgun; container smoke test should be part of acceptance criteria.

**Architecture** (Round 1 — FINDING, all resolved):
(1) `main.py` in wrong directory — API Entrypoints belong in `bootstrapper/` per CLAUDE.md (blocker); moved. (2) Three missing ADRs (blockers): ADR-0003 (Redis idempotency), ADR-0004 (OpenTelemetry), ADR-0005 (OrphanTaskReaper) — all written. Advisory: `shared/middleware` and `shared/tasks` not in import-linter forbidden list (deferred; no module-level imports confirmed). ADR numbering conflict resolved: T2.2 ADR renumbered to ADR-0006; T2.3 ADRs to ADR-0007/0008. Retrospective: file placement BLOCKER validates architecture reviewer role — catching structural violations unit tests cannot detect; ADRs should be written alongside implementation, not as post-review fix.

---

### [2026-03-13] P1-T1.3–1.7 — Docker Infrastructure (base image, security, dev-experience, hardening, air-gap bundler)

**QA** (Round 1 — FINDING, 2 blockers fixed before merge):
Two blockers caught: (1) `CMD ["poetry", "run", "uvicorn", ...]` in Dockerfile final stage called a binary absent from the final image — Poetry installed in builder only; container would crash on every start; fixed to direct `uvicorn` invocation. (2) No `trap ERR` in `build_airgap.sh` — a failed `docker save` would leave a partial `.tar` in `dist/` silently bundled on re-run; `trap ERR` cleanup added. Advisory: no `HEALTHCHECK` instruction (added); `infrastructure_security.md §3` incorrectly justified root requirement as "binding ports < 1024" for port 8000 (corrected). Misleading SC2034 shellcheck disable comment removed. `.env.dev` missing from airgap bundle (copy step added). Retrospective: multi-stage Dockerfile CMD/stage mismatch signals future infra PRs need a `make test-image` container smoke step to surface this class of failure before review.

**UI/UX** (Round 1 — SKIP):
No templates, routes, forms, or interactive elements. Forward: three accessibility pre-conditions from the Docker topology tracked as ADV-016 — CSP headers for React SPA, Jaeger iframe accessibility, MinIO console scope. The frontend-builder Dockerfile stage is the first commitment to a React/Vite architecture; accessibility obligations attached to that commitment are cheapest to address at architecture time.

**DevOps** (Round 1 — PASS):
gitleaks 49 commits, 0 leaks. `cap_drop: ALL`, `read_only: true`, tini PID-1, su-exec, Docker Secrets skeleton all correctly implemented. Advisory fixes applied: bare `print()` in `seeds.py` replaced with `logger.info()`; logger name `"conclave.seeds"` corrected to `__name__`; `entrypoint.sh` echo replaced `$*` with `$1` (latent auth-material logging trap). Advisory: three base images use floating tags (`node:20-alpine`, `python:3.14-slim`, `redis:7-alpine`) — tracked as ADV-014. No Trivy CI step — tracked as ADV-015. Retrospective: the project's habit of pinning Python packages in `pyproject.toml` must extend to Dockerfile FROM lines before Phase 2 ships.

---

### [2026-03-13] P0.8.3 — Spike C: Topological Subset & Referential Integrity

**QA** (Round 1 — FINDING, advisory, non-blocking):
Kahn's algorithm correct; CTE/EXISTS pattern is the right architectural choice over JOINs; streaming memory proof genuine (0.38 MB peak on 81-row subset). Two edge cases flagged for Phase 3: `_infer_pk_column` checks `pk==1` only (wrong for composite-PK tables); `_resolve_reachable` uses "any-parent OR" semantics — correct for downstream-pull subsetting but must be explicitly decided in an ADR before Phase 3. `_build_cte_body` docstring describes `reachable` parameter inaccurately. Ruff S608 suppression gap: four violations in `spikes/` because `# nosec B608` suppresses bandit only, not ruff — requires `"spikes/**" = ["S311", "S608"]` in `[tool.ruff.lint.per-file-ignores]` before Phase 3. Retrospective: `# nosec B608` vs `# noqa: S608` are not interchangeable — this will silently recur when SQL-adjacent code appears in Phase 3 `src/ingestion/` modules.

**UI/UX** (Round 1 — SKIP):
No templates, routes, forms, or interactive elements. Forward: topological subset logic will surface in Phase 5 as relationship visualization. Force-directed graphs are one of the most reliably inaccessible UI patterns — any visual graph must have a text-based equivalent (structured table or adjacency list). Subset size and privacy epsilon budget displayed as status indicators must not rely on color alone to signal threshold warnings.

**DevOps** (Round 1 — PASS):
gitleaks 41 commits, 0 leaks. All fixture PII uses `fictional.invalid` RFC 2606 reserved domain. `nosec B608` annotations carry written justifications in both inline comments and class docstrings — correct suppression annotation practice. Advisory: when `SubsetQueryGenerator` graduates to `src/`, `seed_table` crosses a trust boundary; require allowlist validation against `SchemaInspector.get_tables()` before any f-string SQL construction. Recommend documenting `spikes/` CI carve-out explicitly in ADR or README.

---

### [2026-03-13] P0.8.2 — Spike B: FPE Cipher & LUHN-Preserving Masking

**QA** (Round 1 — FINDING, advisory, non-blocking):
Feistel implementation algorithmically correct — `encrypt`/`decrypt` are proper inverses, zero collisions confirmed. Dead code: `original_cards` parameter in `_run_assertions()` is accepted, documented, then immediately discarded (`_ = original_cards`) — remove before Phase 4 promotion. Unguarded edge cases: `rounds=0` is identity transformation; `luhn_check("")` returns `False` silently; `_luhn_check_digit("")` returns `"0"` silently — none block spike merge, all must be addressed in `tests/unit/test_fpe_luhn.py` (TDD RED) before `masking/fpe.py` lands in `src/`. Retrospective: dead `original_cards` parameter is a canary for leftover refactoring scaffolding — spike-to-production promotion path is currently undocumented; address in `AUTONOMOUS_DEVELOPMENT_PROMPT.md` before Phase 4.

**UI/UX** (Round 1 — SKIP):
No templates, routes, forms, or interactive elements. Forward: when FPE-masked values surface in the Phase 5 dashboard, masked CC numbers in display must carry `aria-label` distinguishing them as synthetic/masked; icon-only controls require non-visual labels; epsilon/privacy-budget gauges must not rely on color alone.

**DevOps** (Round 1 — PASS):
gitleaks 40 commits, 0 leaks. `secrets.token_bytes(32)` key never printed, logged, or serialized. `random.Random(42)` (fixture generation only) annotated `# noqa: S311` + `# nosec B311` with written justification at two levels — correct crypto/PRNG boundary management. All input validation in place (`isdigit()`, length guards). Advisory: `spikes/` outside bandit scan targets — add `.bandit` marker or extend scan path before Phase 4.

---

### [2026-03-13] P0.8.1 — Spike A: ML Memory Physics & OSS Synthesizer Constraints

**QA** (Round 1 — FINDING, advisory, non-blocking):
`_process_chunk()` line 322-323: `except ValueError: pass` swallows malformed numeric cells with no logging, silently skewing fitted mean/variance with zero diagnostic signal. Advisory: add `# noqa: S311` alongside existing `# nosec B311` at lines 379 and 522 to prevent ruff scope-creep failures if `spikes/` is ever added to ruff scan path. Neither finding blocks merge of this spike; the silent-failure pattern must not be carried forward into `src/synth_engine/modules/synthesizer/`. Retrospective: this is the second time a silent swallow has appeared in data-processing hot paths — recommend a codebase-wide convention: any `except` in a data ingestion or transformation path must log at `WARNING` or higher.

**UI/UX** (Round 1 — SKIP):
No templates, routes, forms, or interactive elements. Spike output correctly isolated in `spikes/`. When synthesizer results reach the dashboard: long-running DP-SGD jobs need visible progress feedback and disabled-state double-submission protection; privacy budget parameter forms need programmatic error association.

**DevOps** (Round 1 — PASS):
No secrets, no PII, no new dependencies. `tempfile` cleanup in `finally` block correct. `resource.setrlimit` gracefully degrades on macOS. `nosec B311` annotations carry written justifications. Advisory: numpy fast path uses `np.random.normal` against the global unseeded numpy PRNG — non-deterministic across runs; must be fixed (seed `np.random.default_rng`) before any Phase 4 promotion. Advisory: consider adding `spikes/` to bandit CI scan path.

---

### [2026-03-13] P1-T1.1/1.2 — CI/CD Pipeline, Quality Gates & TDD Framework (3 rounds)

**QA** (Round 3 — PASS):
Clean sweep across all 11 checklist items. chunk_document now has 10 tests covering all boundary conditions including the new negative-chunk_size and negative-overlap guards added in the R1 fix pass. The .secrets.baseline false-positive handling is correct standard detect-secrets practice. The gitleaks.toml allowlist is surgical — path-scoped to .secrets.baseline only, no broad bypasses. 27/27 tests, 100% coverage. Forward watch: as `src/synth_engine/` gains real production code, the 100% figure will become harder to defend; enforce test-file parity from the first production commit rather than retrofitting under deadline pressure. The `importlib.reload()` pattern in scripts/ tests is pragmatic but should not migrate to `src/synth_engine/` proper.

**UI/UX** (Round 3 — SKIP):
No templates, routes, forms, or interactive elements across all three rounds. Infrastructure-only branch. When the dashboard UI lands, establish a `base.html` with landmark regions, skip-link, and CSS custom-property palette as the first commit — retrofitting WCAG across a grown template tree is significantly more expensive than starting from a correct skeleton. Add `pa11y` or `axe-core` to CI at that point.

**DevOps** (Round 3 — PASS):
The .gitleaks.toml path-allowlist is correctly scoped and documented. `gitleaks detect` confirms 34 commits scanned, no leaks. Top-level `permissions: contents: read` in ci.yml closes the default-write-scope gap. Bandit now covers `scripts/` in both pre-commit and CI, eliminating the R1 coverage split. Full gate stack confirmed: gitleaks → lint (ruff+mypy+bandit+vulture+pip-audit+import-linter) → test (poetry run pytest --cov-fail-under=90) → sbom (cyclonedx) → shellcheck. Zero pip-audit vulnerabilities across 135 installed components.

**Architecture** (Round 2 — PASS; Round 3 — SKIP):
All six topology stubs (ingestion, profiler, masking, synthesizer, privacy, shared) present and correctly registered in both import-linter contracts. ADR-0001 accurately describes the modular monolith topology and import-linter enforcement. ADR-0002 accurately describes chromadb as a runtime dependency with air-gap procurement guidance. One standing watch: ADR-0002 references `docs/ARCHITECTURAL_REQUIREMENTS.md` which does not yet exist — tracked as ADV-006. ADRs were written to match code that actually exists, which is the correct practice.

---

### [2026-03-13] P0.6 — Autonomous Agile Environment Provisioning (Round 5)

**QA** (Round 5 — PASS):
Round 5 diff is narrow and correct: chromadb pinned to `chromadb==1.5.5` in CI and `docs/RETRO_LOG.md` created with a well-structured Open Advisory Items table. All 23 tests pass; no source or test code changed. Vulture passes clean on both confidence thresholds. The one latent risk worth elevating: ADV-002's `VERIFICATION_QUERIES[collection_name]` unguarded dict lookup is a real `KeyError` waiting to surface if `SEEDING_MANIFEST` and `VERIFICATION_QUERIES` diverge. It is correctly documented but should be treated as a must-fix (not advisory) when Task 1.1 lands — not something to close casually.

**UI/UX** (Round 5 — SKIP):
No templates, static assets, routes, or interactive elements. Five consecutive SKIP rounds confirm the project is correctly sequencing infrastructure before UI. Key forward recommendation: treat the first `base.html` as a first-class architecture decision — hard-code landmark regions, a skip-to-content link, and heading hierarchy before feature templates proliferate. Add `pa11y` or `axe-core` to CI at that point so WCAG 2.1 AA regressions are machine-caught at the PR gate.

**DevOps** (Round 5 — PASS):
chromadb pin resolves R4 FINDING cleanly with a maintenance comment cross-referencing the pyproject.toml transition. RETRO_LOG.md structured ledger with Open Advisory Items is operationally significant — genuine institutional memory for cross-task findings. One residual observation: `pytest` itself remains unpinned on CI line 74 alongside the now-pinned `chromadb`; captured as ADV-005. gitleaks-action@v2 floating tag (supply-chain note) acceptable at bootstrap stage; recommend SHA-pinning in first full CI hardening pass.

---

### [2026-03-13] P0.6 — Autonomous Agile Environment Provisioning

**QA** (Round 3 — PASS):
This second-round review confirms genuine improvement over round 1: the `print`-to-logger migration, the infinite-loop guard in `chunk_document`, and the `validate_env_file` security gate in `pre_tool_use.sh` are all solid, well-tested work. The pattern worth flagging for future PRs is the asymmetry between `init_chroma.main()` and `seed_chroma.main()`: one has tests and defensive error handling around PersistentClient, the other has neither. This kind of structural sibling-file divergence tends to persist through a codebase when scripts are written incrementally — the team should consider a shared testing fixture or base pattern for ChromaDB-touching scripts so the defensive idioms don't have to be re-invented (and re-reviewed) each time. The weak `assert count > 0` on line 107 of test_seed_chroma.py is a small but real signal that test assertions were not reviewed with the same rigor as the implementation code; establishing a convention of asserting exact return values (not just positivity) in future PRs will improve regression sensitivity.

**UI/UX** (Round 4 — SKIP):
This is the fourth consecutive round with no UI surface to review, which is consistent with the project still being in its infrastructure and tooling phases. The pattern is worth noting positively: the team is building out CI, pre-commit gates, and quality scaffolding before any user-facing code exists. From an inclusive design standpoint, this is the right order of operations — accessibility is far easier to enforce when the pipeline is in place to catch regressions before they land. The open risk remains that when templates and interactive elements do arrive, there will be pressure to move quickly; the CI pipeline added in this round should be extended at that point to include an automated accessibility linter (such as axe-core or pa11y) so that WCAG 2.1 AA compliance is a machine-checked gate, not just a manual review step.

**DevOps** (Round 3 — PASS):
The Round 3 fixes were clean and precise — both ImportError guards were correctly converted to `sys.stderr.write()` with no residual `print()` calls, and the credential variable substitution pattern in `worktree_create.sh` is exactly right. The security posture of this diff is strong for a pre-framework bootstrap phase: no secrets, no PII, no bypass flags, and the `.env.local` validation in `pre_tool_use.sh` shows deliberate defense-in-depth thinking (rejecting command substitution in sourced files is non-trivial and is the right call). The one open operational risk is the absent CI pipeline — with tests now in the repository, there is no automated guard ensuring they stay green across branches. That gap should be closed at the start of Phase 1 alongside `pyproject.toml`, not deferred further; the longer CI is absent, the more likely a regression will slip into `main` undetected.
