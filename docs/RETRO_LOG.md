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
| ADV-011 | QA P0.8.2 | Before Phase 4 (masking module) | `FeistelFPE` in `spike_fpe_luhn.py` has unguarded edge cases: `rounds=0` is an identity transformation (no encryption). Also document spike-to-production promotion checklist in `AUTONOMOUS_DEVELOPMENT_PROMPT.md` before Phase 4. (Note: `luhn_check("")` edge case resolved in T3.3 production implementation.) |
| ADV-014 | DevOps P1-T1.3–1.7 | Before Phase 2 ships | Dockerfile FROM lines for `node:20-alpine`, `python:3.14-slim`, and `redis:7-alpine` use floating minor-version tags. A silent tag update can introduce new packages or CVEs without triggering a dependency review. Pin all FROM lines to SHA-256 digests (e.g. `python:3.14-slim@sha256:<digest>`) before any production deployment. |
| ADV-015 | DevOps P1-T1.3–1.7 | Standalone CI hardening task | No Trivy image-scan job in `ci.yml`. The Dockerfile comment notes a manual trivy scan but this is unenforced. Add `aquasecurity/trivy-action` to CI with `exit-code: 1` on CRITICAL/HIGH CVEs — makes the image-CVE gate as automatic as bandit and pip-audit. Bundle with ADV-007 (SHA-pin GitHub Actions) into a single CI hardening pass. |
| ADV-016 | UI/UX P1-T1.3–1.7 | Before Phase 5 dashboard task | Three accessibility pre-conditions from the Docker topology: (1) CSP headers for React/Vite SPA must be established in FastAPI middleware before frontend build starts — restrictive `script-src 'self'` will block inline scripts used by accessibility polyfills; (2) any Jaeger iframe embed needs `<iframe title="...">` and documented third-party WCAG scope exclusion; (3) MinIO console must be treated as internal developer tool only — never surfaced to end users. |
| ADV-017 | DevOps P2-T2.4 | Before Phase 5 (T5.3 React SPA) | `details: dict[str,str]` on `AuditEvent` is an open PII sink — any key/value can be written to the WORM log without validation. Add a Pydantic validator or key allowlist to `AuditEvent` before the event surface area grows beyond its one current call site. |
| ADV-018 | UI/UX P2-T2.4 | Before Phase 5 T5.3 (Vault Unseal UI) | `POST /unseal` returns undifferentiated `400` for both wrong-passphrase and missing-VAULT_SEAL_SALT config errors. Phase 5 UI needs a structured error code (e.g. `{"detail": "...", "code": "WRONG_PASSPHRASE" \| "CONFIG_ERROR"}`) to route operators to correct remediation. Add structured error codes before the first template renders `/unseal` responses. |
| ADV-019 | UI/UX P2-T2.4 | Before Phase 5 T5.3 (Vault Unseal UI) | `POST /unseal` triggers 600k-iteration PBKDF2 (~0.5–1s CPU). The Phase 5 form must disable the submit button immediately on POST and show a loading indicator to prevent double-submit. Establish this UI contract before the React SPA is built. |
| ADV-031 | QA P3-T3.5 | Before T5.1 (POST /subset API route) | T3.5 AC requires tests to "invoke the Subsetting API endpoint or CLI entrypoint representing a complete user job." No such endpoint exists in Phase 3 (bootstrapper exposes only /health and /unseal). Direct `SubsettingEngine.run()` calls are accepted as the Phase 3 stand-in. This AC must be satisfied when T5.1 builds the `POST /subset` endpoint — the E2E test suite should be extended to call the HTTP layer at that point. |
| ADV-021 | QA P2-D2 | Before Phase 3/4 TypeDecorator usage | `EncryptedString` NULL passthrough, empty-string, and unicode/multi-byte PII paths are not exercised at the integration level (only unit-tested). Also: `Fernet.InvalidToken` propagation through SQLAlchemy on a live connection is untested. Write targeted integration tests for these edge cases before additional TypeDecorators are added in Phase 3/4. |
| ADV-022 | DevOps P3-T3.2 | Before bootstrapper T3.4/Phase 4 API layer | `CycleDetectionError` messages embed table names (structural metadata, not PII) — they must not reach external API callers verbatim. The bootstrapper layer must intercept `CycleDetectionError` from `topological_sort()` and return a structured API response. Raw schema names must not appear in HTTP responses to prevent information disclosure about internal database topology. |
| ADV-028 | Arch P3-T3.4 | Before Phase 4 multi-consumer topology injection | `SchemaTopology.columns` and `foreign_keys` fields are typed as `dict[str, ...]` in a `frozen=True` dataclass — `frozen=True` guards field reassignment but not dict mutation. A downstream module could mutate the shared topology via `topology.columns['x'] = ()` with no error. Convert field types to `Mapping[str, tuple[...]]` or wrap dicts in `types.MappingProxyType` in `__post_init__` before Phase 4 introduces multiple concurrent consumers of the same topology instance. |
| ADV-029 | DevOps P3-T3.4 | Before bootstrapper wiring (Phase 4/5 API layer) | `EgressWriter.rollback()` produces no log output. If TRUNCATE CASCADE itself throws (lock timeout, permission error on target), the exception propagates with no record of which tables were in `_written_tables` at the time. Add a structured `WARNING` log at rollback entry recording `_written_tables` before Phase 4 bootstrapper wires the engine into an API route. |
| ADV-030 | DevOps P3-T3.4 | Follow-up cleanup task | `tests/integration/test_subsetting_integration.py` `_create_database()` helper uses `f'CREATE DATABASE "{dbname}"'` (string formatting with `# nosec B608`), while sibling `_drop_database()` correctly uses `psycopg2.extensions.quote_ident`. Harmonise `_create_database()` to use `quote_ident` for consistency within the file. |
| ADV-025 | Arch P3-T3.3 | Before Phase 4 masking integration | `luhn_check` lives in `algorithms.py` alongside Faker-driven functions, mixing abstraction levels. CLAUDE.md's own canonical example explicitly names `masking/luhn.py`. Move to a dedicated `src/synth_engine/modules/masking/luhn.py` before Phase 4 tasks extend the masking module. |
| ADV-026 | Arch P3-T3.3 | Before Phase 4 masking integration | `deterministic_hash()` in `deterministic.py` accepts a `length` parameter with no guard against `length > 32`. HMAC-SHA256 produces exactly 32 bytes; `digest()[:length]` silently returns a shorter slice if `length > 32`. Add: `if length > 32: raise ValueError("length exceeds SHA-256 digest size (32)")`. |
| ADV-027 | DevOps P3-T3.3 | Before any export of masked data outside air-gapped environment | The HMAC "key" in `deterministic_hash()` is a predictable schema-derived string (e.g. `"users.email"`), not a secret. The real-to-fake PII mapping is reversible by anyone with schema knowledge and the Faker version. Acceptable within the air-gapped engine; if masked data is ever exported to a less-trusted context, add a threat-model entry to ADR-0014. Also: the module-level `_FAKER` singleton is not thread-safe; re-evaluation required before any async or multi-threaded pipeline is introduced. |

---

## Task Reviews

---

### [2026-03-14] P3-T3.5 — E2E Subsetting Subsystem Tests

**QA** (FINDING, all resolved):
Coverage gate passed (287 unit tests, 97.89%). Three findings resolved: (1) `row_transformer` returning `None` would silently produce `[None, ...]` passed to egress — explicit loop with `None` guard added to `core.py`; raises `TypeError` with table name context; `test_transformer_none_return_raises_type_error` added. (2) `row_transformer` raising an exception not tested as triggering rollback — `test_transformer_failure_triggers_rollback` added. (3) Backlog AC gap: T3.5 spec requires tests to "invoke the Subsetting API endpoint or CLI entrypoint" — no such endpoint exists in Phase 3. PM ruling: AC is aspirational; direct `SubsettingEngine.run()` calls are the correct Phase 3 stand-in. This AC will be satisfied when T5.1 builds `POST /subset`. Tracked as ADV-031. Retrospective: new injectable Callable parameters need unit tests for (1) well-behaved, (2) raising, and (3) None/invalid-return scenarios — standing checklist item warranted.

**UI/UX** (SKIP):
Pure backend: callback parameter extension + integration test file. No UI surface area.

**DevOps** (FINDING, fixed):
All secrets hygiene checks pass. Fictional PII patterns in fixtures. FINDING (fixed): CI had no integration test job — `tests/integration/` was never executed in the automated pipeline, making the E2E tests meaningless as a CI gate. `integration-test` job added to `ci.yml` with `services: postgres:16-alpine`, health checks, `poetry install --with dev,integration`, and `pytest tests/integration/`. This closes ADV-020 (standing since P2-D2) for all existing integration tests simultaneously. `.secrets.baseline` updated for `POSTGRES_PASSWORD: postgres` fixture constant (detect-secrets false positive). Retrospective: third consecutive PR adding integration tests without CI wiring; the `_require_postgresql` comment "In CI the PostgreSQL service is always present" was factually incorrect until this fix.

**Architecture** (FINDING, all resolved):
Import-linter contracts fully preserved — `core.py` does not import from `modules/masking`; transformer injected via constructor IoC. Two findings resolved: (1) `# type: ignore` suppressions in test files lacked inline justification comments per CLAUDE.md — justifications added to all occurrences in both `test_e2e_subsetting.py` and `test_subsetting_integration.py`. (2) ADR-0015 had no documentation of the `row_transformer` IoC injection pattern — §7 "row_transformer Injection Contract" added documenting: IoC rationale, callback signature and purity contract, bootstrapper responsibility, and cross-reference to ADR-0014. Retrospective: `row_transformer` is the canonical Phase 4 cross-module wiring pattern; documenting it in ADR-0015 before Phase 4 starts is time-sensitive — bootstrapper authors now have an authoritative contract.

---

### [2026-03-14] P3-T3.4 — Subsetting & Materialization Core

**QA** (Two passes — FINDING, all resolved):
Coverage gate passed (285 unit tests, 98.23%). Findings across both passes resolved:
(1) Eight uncovered branch guards in `traversal.py` (nullable FK column path, no-PK-in-topology path, parent-not-yet-fetched continue branch) — three new unit tests added covering all critical production paths. (2) `EgressWriter.commit()` no-op lacked direct test; INSERT failure propagation from `write()` unhappy path untested — `test_commit_is_noop` and `test_write_propagates_sqlalchemy_error` added. (3) Rubber-stamp `call_count >= 1` replaced with `== len(rows)`. (4) **Second-pass FINDING:** Integration-level Saga rollback test was absent despite explicit backlog AC ("partial write failure → target left clean") — `test_saga_rollback_leaves_target_clean` added to `tests/integration/`; uses real pytest-postgresql, patches `EgressWriter.write()` to fail on second table, asserts zero rows in all target tables post-failure. Advisory: `SchemaTopology` dict mutability under `frozen=True` dataclass (ADV-028). Retrospective: Internal branch guards for production-reachable edge cases (nullable FKs, PK-less topology) were systematically left untested. The backlog's explicit integration-test AC was satisfied only at mock level in the first pass — a second-reviewer pass caught the gap; this pattern confirms that backlog AC items specifying real-DB tests need the QA reviewer to verify the test file directly, not just assert coverage %.

**UI/UX** (SKIP):
Pure backend data pipeline, no UI surface area. Forward: when egress and materialization results surface in a dashboard (Phase 5), the rich failure modes encoded in `core.py` and `egress.py` will need deliberate accessible design — loading states, error-region announcements, and accessible data table markup should be planned before implementation begins.

**DevOps** (PASS — one second-pass FINDING fixed):
gitleaks clean. Test fixtures use synthetic fictional data only. No hardcoded credentials — integration tests use pytest-postgresql ephemeral proc fixtures. Bandit 0 issues; `nosec B608` suppressions correctly scoped to `quoted_name`-protected identifier construction. **Second-pass FINDING (fixed):** `seed_query` parameter executed verbatim via `text()` with no SELECT-only guard — `seed_query.strip().upper().startswith("SELECT")` guard added to `SubsettingEngine.run()` with two new unit tests. Advisory: Saga rollback path produces no log output — `_written_tables` state at rollback time should be logged at WARNING before bootstrapper wiring (ADV-029). Advisory: `_create_database()` in integration test uses string formatting while sibling uses `quote_ident` — harmonise (ADV-030).

**Architecture** (FINDING, all resolved):
File placement correct (`shared/schema_topology.py`, `modules/ingestion/core.py`, `egress.py`, `traversal.py`). Import-linter contracts all satisfied: independence, no-bootstrapper, shared-no-modules. Bootstrapper-as-value-courier pattern executed correctly: `SchemaTopology` in `shared/` with zero module imports; `SubsettingEngine` receives it via constructor without importing `SchemaReflector` or `DirectedAcyclicGraph`. Two FINDINGs resolved: (1) `transversal.py` filename was a misspelling — renamed to `traversal.py` via `git mv`; import updated in `core.py`. (2) ADR-0015 missing async call-site contract section (established as project precedent in ADR-0012 post-T3.1 arch review) — §6 "Async Call-Site Contract" added to ADR-0015 with canonical `asyncio.to_thread()` example; `run()` docstring updated. ADV-023 and ADV-024 (inspector caching, `# type: ignore` justification in `reflection.py`) both resolved in this task. Retrospective: Cleanest cross-module boundary implementation in Phase 3 — `SchemaTopology` placement and constructor injection pattern should be the canonical reference for downstream modules in Phase 4.

---

### [2026-03-14] P3-T3.3 — Deterministic Masking Engine

**QA** (Round 1 — FINDING, all resolved):
Coverage gate passed (99.35%, 185 tests). Four FINDINGs resolved: (1) `_apply()` match/case in `registry.py` had no wildcard `case _:` arm — new `ColumnType` values silently returned `None`, violating `-> str` annotation; fixed with `case _: raise ValueError(...)` + test for unreachable enum value. (2) Vacuous assert `assert result_a != result_b or True` in `test_masking_deterministic.py` — the `or True` made it a no-op; replaced with set-based uniqueness check across 10 distinct inputs. (3) `luhn_check("")` empty-string branch uncovered; `luhn_check` non-digit input also uncovered — `test_luhn_check_empty_string` and `test_luhn_check_non_digit_input` added. (4) `CollisionError` raise path (defensive guard, provably unreachable via monotonically-incrementing suffix counter) — marked `# pragma: no cover` with explanatory comment. Both mandatory backlog tests present: 100,000-record zero-collision assertion and LUHN credit card verifier. Retrospective: the vacuous `or True` pattern creates the appearance of probabilistic test coverage without providing it; watch for this in future PRs touching heuristic or stochastic test cases. The `luhn_check("")` miss is consistent with the test suite otherwise being comprehensive.

**UI/UX** (Round 1 — SKIP):
No UI surface. Forward: future interface PRs touching the masking subsystem should anticipate non-trivial accessibility demands — field-type selectors, algorithm configuration forms, and audit-trail displays carry real WCAG 2.1 AA surface area.

**DevOps** (Round 1 — PASS):
gitleaks clean. Test fixtures use known-safe values (555- prefix phone, 411... Visa test card, fictional SSN format). Zero logging calls in masking module — no PII leak path. Bandit 0 issues. One new dependency (`faker ^40.11.0`) — pip-audit clean, no CVEs. Pre-commit mypy isolated environment patched (faker added to `additional_dependencies` in `.pre-commit-config.yaml` — was declared as production dep but not registered in pre-commit's mypy env). Advisory: HMAC "key" is a predictable schema-derived string; reversibility concern in less-trusted export contexts (ADV-027). Thread-safety of `_FAKER` singleton must be re-evaluated before async pipeline (ADV-027).

**Architecture** (Round 1 — FINDING, all resolved):
File placement correct (`modules/masking/`). Import-linter contracts correctly updated: independence, forbidden-from-bootstrapper, and shared-forbidden all wired. One FINDING resolved: `_apply()` missing `case _:` default (same as QA finding) — now raises `ValueError`. `faker` IS declared in `[tool.poetry.dependencies]` (confirmed). Advisories: `luhn_check` should move to `luhn.py` per CLAUDE.md canonical example (ADV-025); `deterministic_hash()` lacks `length > 32` guard (ADV-026).

---

### [2026-03-14] P3-T3.1 — Ingestion Engine (PostgreSQL adapter, SSL enforcement, privilege pre-flight)

**DevOps** (Round 1 — FINDING, fixed):
Credential leak: `ValueError` messages in `validators.py` used `{url!r}` — embedded passwords from connection strings in exception messages. Fixed: `_sanitize_url()` helper added, strips `userinfo` component from URL via `urlparse._replace`; all error messages now use sanitized URL. Seven new unit tests verify credentials never appear in error messages. Bandit clean. gitleaks clean.

**QA** (Round 1 — FINDING, all resolved):
Coverage gate passed (99.16%, 169 unit tests; 181 after fixes). Three FINDINGs resolved: (1) Edge-case gaps — `stream_table` with empty table (zero rows): generator exhausts immediately, no test; `preflight_check` only tested INSERT grant, not UPDATE or DELETE individually; `validate_connection_string` not tested for `sslmode=allow` or `sslmode=disable` on remote hosts. Five new tests added covering all three gaps. (2) `stream_table` docstring referenced `:meth:get_schema_inspector` in the table-validation description — correct reference is `_validate_table_name` (ADV-013 compliance); corrected. (3) `_provision_test_db` fixture annotated `-> None` but contains `yield` — corrected to `-> Generator[None, None, None]`. Retrospective: docstring cross-references to method names go stale quickly — the stream_table error appeared within the same PR the code was written; doc review should be a discrete checklist step. The privilege-check design is correct: `current_setting('is_superuser')` is the right PostgreSQL idiom; ADR-0012 documents the role-inherited-privilege gap honestly.

**Architecture** (Round 1 — FINDING, all resolved):
File placement correct (`modules/ingestion/`). Import-linter contracts satisfied. Three FINDINGs resolved: (1) `stream_table()` and `preflight_check()` are synchronous — deliberate per ADR-0012, but ADR-0012 lacked the `asyncio.to_thread()` call-site contract for callers in async contexts (bootstrapper, orchestrators). Same class of bug caught in T2.1 (Redis blocking event loop) and T2.4 (PBKDF2). ADR-0012 amended with "Async Call-Site Contract" section. (2) ADR-0012 did not document how `SchemaInspector` output crosses module boundaries to T3.2/downstream modules. Per ADR-0001, direct import of `SchemaInspector` by any other module fails import-linter CI. ADR-0012 amended with "Cross-Module Schema Data Flow" section (bootstrapper-as-value-courier pattern). (3) `# type: ignore[return-value]` on `get_columns()` and `get_foreign_keys()` lacked written justification — prose comments added. Advisory: `SchemaInspector` re-creates `inspect(engine)` on each of 3 method calls; caching in `__init__` reduces round-trips (ADV-023). `stream_table` Generator annotation completed to `Iterator[list[dict[str, Any]]]`.

**UI/UX** (Round 1 — SKIP):
No UI surface in this diff. All changes are backend Python modules.

---

### [2026-03-14] P3-T3.2 — Relational Mapping & Topological Sort

**QA** (Round 1 — FINDING, all resolved):
Backlog compliance and coverage gate both passed (98.60%, 174 tests). Two FINDINGs resolved: (1) `add_edge()` non-idempotency — duplicate edges possible from composite/redundant FK constraints; fixed with `_edge_set` for O(1) deduplication and early return; 5 new idempotency tests added and passing. (2) `_find_cycle()` unreachable `return []` at line 213 — replaced with `raise AssertionError` that documents the broken-invariant case explicitly. (3) `has_cycle()` docstring stated "DFS approach" when implementation actually calls `topological_sort()` (Kahn's/BFS) — corrected. Advisories: `# type: ignore` comments on `get_columns`/`get_foreign_keys` lack written justification (ADV-024); `CycleDetectionError` table names must not reach external API callers verbatim (ADV-022).

**UI/UX** (Round 1 — SKIP):
No UI surface in this diff. Forward note: if relational mapping output is exposed through a dashboard (schema graph visualization or dependency table), those components carry non-trivial WCAG 2.1 AA obligations. Complex graph UIs are among the hardest accessibility requirements to satisfy correctly.

**DevOps** (Round 1 — PASS):
gitleaks clean. No PII in node identifiers ("email" string in tests is a column-name key, not an address). No logging calls; no async blocking; no new dependencies. Bandit 0 issues. Advisory: `CycleDetectionError` messages embed table names — must not reach external callers verbatim (ADV-022). CI unchanged; existing pipeline covers new tests.

**Architecture** (Round 1 — FINDING, all resolved):
File placement correct: `graph.py` and `reflection.py` in `modules/ingestion/` as prescribed. One FINDING resolved: ADR-0013 amended with Section 5 (Inter-Module Data Handoff) documenting that bootstrapper must call `SchemaReflector.reflect()` and `topological_sort()` at job-init, package results into a neutral `shared/` dataclass or TypedDict, and inject into downstream modules via constructor. Direct import of DAG types from `modules/ingestion/` by any other module will fail import-linter CI. Cross-references ADR-0001 and ADR-0012. Advisory: cache SQLAlchemy inspector in `SchemaReflector.__init__` (ADV-023).

---

### [2026-03-14] P2 Debt — D2: pytest-postgresql ALE integration test (closes T2.2 backlog gap)

**QA** (Round 1 — PASS):
Both T2.2 AC items satisfied: (1) `test_raw_sql_returns_ciphertext` inserts via ORM then queries via `engine.connect() + text()`, asserting raw value ≠ plaintext and starts with `gAAAAA`; (2) `test_orm_query_returns_plaintext` asserts `loaded.pii_value == original_plaintext`. Tests live in `tests/integration/`, use a real ephemeral PostgreSQL 17 instance, and ran in 2.47s. Two advisory gaps noted: NULL/empty/unicode PII paths not exercised at integration level; `Fernet.InvalidToken` propagation through SQLAlchemy on live connection untested. Neither required by T2.2 AC. Tracked as ADV-021.

**UI/UX** (Round 1 — SKIP):
Test-only PR, no UI surface. One forward note: ALE error states (key rotation failures, decryption errors) will need to meet error-messages criteria if surfaced in Phase 5 UI; test fixture plaintext strings could inform copy for those states.

**DevOps** (Round 1 — PASS):
All secrets hygiene clean — `Fernet.generate_key()` at runtime, `pragma: allowlist secret` annotated, no literal credentials. SQL injection: all parameterised via `text()` + named dicts; `DROP DATABASE` uses `psycopg2.extensions.quote_ident` on a compile-time constant with inline reasoning comment. Bandit 0 findings. Advisory: CI has no `services: postgres:` job — ALE encryption invariant is never CI-verified. Tracked as ADV-020; bundle with ADV-007/ADV-015 CI hardening pass.

**Architecture**: SKIP — no `models/`, `agents/`, `api/`, or new `src/` files touched.

**Phase 2 status**: All debt items resolved (D1/D3/D4 code fixes + D2 integration test). Phase 2 is fully closed. ADV-020 and ADV-021 tracked in Open Advisory Items above.

---

### [2026-03-14] P2 Debt — D1/D3/D4: ALE-Vault wiring, AuditLogger singleton, zero test warnings

Three technical debt items identified in the Phase 2 end-of-phase retrospective, addressed before Phase 3.

**D1 — ALE-Vault KEK wiring via HKDF (PR #11)**:
`get_fernet()` now derives the ALE sub-key from the vault KEK via HKDF-SHA256 (`salt=b"conclave-ale-v1"`, `info=b"application-level-encryption"`) when the vault is unsealed, and falls back to `ALE_KEY` env var when sealed. `@lru_cache` removed — caching across vault state transitions was incorrect. ADR-0006 updated with HKDF parameter table and key rotation implications. Root cause: T2.2 and T2.4 developed in parallel with no cross-task integration matrix check; PM brief did not specify wiring requirement.

**D3 — AuditLogger module-level singleton (PR #12)**:
`get_audit_logger()` now returns a module-level singleton protected by `threading.Lock`. Each call previously returned a new instance, resetting the hash chain on every request — making the WORM property meaningless in any multi-request scenario. `reset_audit_logger()` added for test isolation (TEST USE ONLY). ADR-0010 updated with singleton design, threading.Lock rationale, and process-restart caveat. Root cause: original implementation tested in isolation; cross-request behavior never exercised.

**D4 — Zero test suite warnings (PR #13)**:
`filterwarnings = ["error"]` baseline added to `pyproject.toml`. 173 third-party warnings (pytest-asyncio 0.26.x + chromadb 1.5.x on Python 3.14) eliminated via targeted per-package suppression. Test suite now fails on any new warning, making warning regression impossible to miss silently.

**Process fix**: Two constitutional amendments committed (`docs: amend CLAUDE.md and qa-reviewer`): (1) PM must paste backlog Testing & Quality Gates verbatim into every agent prompt; (2) QA reviewer now has a mandatory `backlog-compliance:` checklist that treats missing integration tests as BLOCKER regardless of coverage %.

Retrospective: All three debt items trace to the same root cause — parallel task development without a cross-task integration matrix review. The process fix (explicit cross-task integration check before presenting any plan) directly addresses this. The one standing watch: D2 (pytest-postgresql integration test for ALE encryption round-trip) is still pending — it is the only item from the Phase 2 retro whose resolution requires new infrastructure (real PostgreSQL + raw SQL query), not just code fixes.

---

### [2026-03-13] P2-T2.4 — Vault Unseal API, WORM Audit Logger, Prometheus/Grafana Observability

**QA** (Round 1 — FINDING, all resolved):
Security primitives (PBKDF2-HMAC-SHA256 at 600k iterations, bytearray zeroing, HMAC-SHA256 chaining, `compare_digest`) correctly implemented. Two blockers resolved: (1) `except (ValueError, Exception)` narrowed to `except ValueError` — broad clause was treating `MemoryError`/programming errors as HTTP 400; (2) empty-passphrase guard and re-unseal guard added to `VaultState.unseal()` — state-boundary edge cases previously untested. `require_unsealed()` happy-path test added. Forward: future PRs touching `VaultState` should include a state-machine test table covering all `(initial_state, input) → (final_state, output)` combinations. Exception-scope drift in HTTP handlers is a recurring pattern to watch — catching broadly for "robustness" produces opaque failures that defeat the sealed-vault security model.

**UI/UX** (Round 1 — SKIP):
No templates, forms, or interactive elements. Two API contract findings (advisory): (1) `str(exc)` in 400 response body leaks env var names — must be mapped to generic message at Phase 5 UI layer; (2) wrong-passphrase and config-error both return bare 400 — structured error code (`code: "WRONG_PASSPHRASE" | "CONFIG_ERROR"`) needed before Phase 5 template renders `/unseal` responses. Sixth consecutive SKIP; infrastructure-before-UI sequencing remains disciplined.

**DevOps** (Round 1 — FINDING, all resolved):
Cryptographic foundation solid. Four findings resolved: (1) `asyncio.to_thread()` wrapping added for PBKDF2 (was blocking event loop ~0.5–1s); (2) `GF_SECURITY_ADMIN_USER__FILE` added to Grafana service in docker-compose (username was defaulting to "admin"); (3) `"conclave.audit"` logger renamed to `"synth_engine.security.audit"` — `conclave.*` names were outside the PIIFilter hierarchy; (4) `pydantic` added as direct dep (was transitive via sqlmodel, fragile). Advisory: `details: dict[str,str]` on `AuditEvent` is an open PII sink — tracked as ADV-017.

**Architecture** (Round 1 — FINDING, all resolved):
Boundary discipline strong — `shared/` has zero FastAPI/bootstrapper imports; import-linter reverse guard satisfied throughout. Three findings resolved: (1) `except (ValueError, Exception)` blocker (see QA); (2) `get_audit_logger()` docstring clarified re: chain isolation per call; (3) `pydantic` direct dep added. Standing watch: `VaultState` as a pure-classmethods class is effectively a module-level namespace — acceptable for this use case (single-instance service) but must not be mixed with injectable-instance patterns in Phase 5.

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
No templates, static assets, routes, or interactive elements. Five consecutive SKIP rounds confirm the project is correctly sequencing infrastructure before UI. Key forward recommendation: treat the first `base.html` as a first-class architecture decision — hard-code landmark regions, a skip-to-content link, and heading hierarchy before feature templates proliferate. Add `pa11y` or `axe-core` to CI at that point so WCAG 2.1 AA compliance is a machine-checked gate, not just a manual review step.

**DevOps** (Round 5 — PASS):
chromadb pin resolves R4 FINDING cleanly with a maintenance comment cross-referencing the pyproject.toml transition. RETRO_LOG.md structured ledger with Open Advisory Items is operationally significant — genuine institutional memory for cross-task findings. One residual observation: `pytest` itself remains unpinned on CI line 74 alongside the now-pinned `chromadb`; captured as ADV-005. gitleaks-action@v2 floating tag (supply-chain note) acceptable at bootstrap stage; recommend SHA-pinning in first full CI hardening pass.

---

### [2026-03-13] P0.6 — Autonomous Agile Environment Provisioning

**QA** (Round 3 — PASS):
This second-round review confirms genuine improvement over round 1: the `print`-to-logger migration, the infinite-loop guard in `chunk_document`, and the `validate_env_file` security gate in `pre_tool_use.sh` are all solid, well-tested work. The pattern worth flagging for future PRs is the asymmetry between `init_chroma.main()` and `seed_chroma.main()`: one has tests and defensive error handling around PersistentClient, the other has neither. This kind of structural sibling-file divergence tends to persist through a codebase when scripts are written incrementally — the team should consider a shared testing fixture or base pattern for ChromaDB-touching scripts so the defensive idioms don't have to be re-invented (and re-reviewed) each time. The weak `assert count > 0` on line 107 of test_seed_chroma.py is a small but real signal that test assertions were not reviewed with the same rigor as the implementation code; establishing a convention of asserting exact return values (not just positivity) in future PRs will improve regression sensitivity.

**UI/UX** (Round 4 — SKIP):
This is the fourth consecutive round with no UI surface to review, which is consistent with the project still being in its infrastructure and tooling phases. The pattern is worth noting positively: the team is building out CI, pre-commit hooks, and quality scaffolding before any user-facing code exists. From an inclusive design standpoint, this is the right order of operations — accessibility is far easier to enforce when the pipeline is in place to catch regressions before they land. The open risk remains that when templates and interactive elements do arrive, there will be pressure to move quickly; the CI pipeline added in this round should be extended at that point to include an automated accessibility linter (such as axe-core or pa11y) so that WCAG 2.1 AA compliance is a machine-checked gate, not just a manual review step.

**DevOps** (Round 3 — PASS):
The Round 3 fixes were clean and precise — both ImportError guards were correctly converted to `sys.stderr.write()` with no residual `print()` calls, and the credential variable substitution pattern in `worktree_create.sh` is exactly right. The security posture of this diff is strong for a pre-framework bootstrap phase: no secrets, no PII, no bypass flags, and the `.env.local` validation in `pre_tool_use.sh` shows deliberate defense-in-depth thinking (rejecting command substitution in sourced files is non-trivial and is the right call). The one open operational risk is the absent CI pipeline — with tests now in the repository, there is no automated guard ensuring they stay green across branches. That gap should be closed at the start of Phase 1 alongside `pyproject.toml`, not deferred further; the longer CI is absent, the more likely a regression will slip into `main` undetected.
