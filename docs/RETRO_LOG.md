# Conclave Engine — Retrospective Log

Living ledger of review retrospective notes and open advisory items.
Updated after each task's review phase completes.

---

## Open Advisory Items

Advisory findings without a resolved target task are tracked here.
Drain (delete) rows when their target task is completed.

| ID | Source | Target Task | Advisory |
|----|--------|-------------|----------|
| ADV-011 | QA P0.8.2 | Before Phase 4 promotion of spike_fpe_luhn.py | `FeistelFPE` in `spike_fpe_luhn.py` has unguarded edge cases: `rounds=0` is an identity transformation (no encryption). Guard must be added before promotion. Promotion checklist documented in CLAUDE.md (Phase 4 Kickoff). |
| ADV-014 | DevOps P1-T1.3–1.7 | Before Phase 2 ships | Dockerfile FROM lines for `node:20-alpine`, `python:3.14-slim`, and `redis:7-alpine` use floating minor-version tags. A silent tag update can introduce new packages or CVEs without triggering a dependency review. Pin all FROM lines to SHA-256 digests (e.g. `python:3.14-slim@sha256:<digest>`) before any production deployment. |
| ADV-016 | UI/UX P1-T1.3–1.7 | Before Phase 5 dashboard task | Three accessibility pre-conditions from the Docker topology: (1) CSP headers for React/Vite SPA must be established in FastAPI middleware before frontend build starts — restrictive `script-src 'self'` will block inline scripts used by accessibility polyfills; (2) any Jaeger iframe embed needs `<iframe title="...">` and documented third-party WCAG scope exclusion; (3) MinIO console must be treated as internal developer tool only — never surfaced to end users. |
| ADV-017 | DevOps P2-T2.4 | Before Phase 5 (T5.3 React SPA) | `details: dict[str,str]` on `AuditEvent` is an open PII sink — any key/value can be written to the WORM log without validation. Add a Pydantic validator or key allowlist to `AuditEvent` before the event surface area grows beyond its one current call site. |
| ADV-018 | UI/UX P2-T2.4 | Before Phase 5 T5.3 (Vault Unseal UI) | `POST /unseal` returns undifferentiated `400` for both wrong-passphrase and missing-VAULT_SEAL_SALT config errors. Phase 5 UI needs a structured error code (e.g. `{"detail": "...", "code": "WRONG_PASSPHRASE" \| "CONFIG_ERROR"}`) to route operators to correct remediation. Add structured error codes before the first template renders `/unseal` responses. |
| ADV-019 | UI/UX P2-T2.4 | Before Phase 5 T5.3 (Vault Unseal UI) | `POST /unseal` triggers 600k-iteration PBKDF2 (~0.5–1s CPU). The Phase 5 form must disable the submit button immediately on POST and show a loading indicator to prevent double-submit. Establish this UI contract before the React SPA is built. |
| ADV-021 | QA P2-D2 | Before Phase 3/4 TypeDecorator usage | `EncryptedString` NULL passthrough, empty-string, and unicode/multi-byte PII paths are not exercised at the integration level (only unit-tested). Also: `Fernet.InvalidToken` propagation through SQLAlchemy on a live connection is untested. Write targeted integration tests for these edge cases before additional TypeDecorators are added in Phase 3/4. |
| ADV-035 | DevOps T3.5.4 | T4.x (masking configuration) | `_CLI_MASKING_SALT` in `bootstrapper/cli.py` is a hardcoded fallback determinism seed (documented as non-secret). When Phase 4 introduces the production `MASKING_SALT` path, the CLI must be updated to inject it from environment/Vault rather than relying on the hardcoded fallback. Remove the hardcoded fallback or gate it behind a strict non-production check. |
| ADV-036 | DevOps T3.5.4 | T5.1 (CLI hardening) | The CLI's `except Exception` boundary forwards `str(exc)` from potentially deep SQLAlchemy stack frames directly to the operator. As the engine grows, those exception messages could include table names, column names, or query fragments from customer schemas. Revisit the exception sanitisation boundary at T5.1 when the CLI is hardened for production use. |
| ADV-037 | Arch+QA P4-T4.1 | T4.2b | **BLOCKER (CLAUDE.md Rule 8)**: `EphemeralStorageClient` wiring deferred — `MinioStorageBackend` is not wired into the bootstrapper because `SynthesisEngine` does not yet exist. `TODO(T4.2b)` comment added to `bootstrapper/main.py`. Must be completed before T4.2b merges; T4.2b is a Phase 4 entry gate for this wiring. |

---

## Task Reviews

---

### [2026-03-14] P4-T4.2a — Statistical Profiler

**Architecture** (FINDING, 2 fixed):
file-placement PASS. naming-conventions FINDING (fixed) — `_QUANTILES` constant defined but unused; replaced inline literal with `list(_QUANTILES)`. dependency-direction PASS — no cross-module imports; import-linter 4/4 kept. abstraction-level PASS — stateless class, models.py/profiler.py split appropriate. interface-contracts FINDING (fixed) — `ProfileDelta`/`ColumnDelta` had `to_dict()` but no `from_dict()`; asymmetric contract breaks consumer round-trips; `from_dict()` added to both with round-trip tests. model-integrity PASS — frozen=True on all four models. adr-compliance PASS. Advisory: no ADR covers the profiler's role as drift oracle — when bootstrapper wiring lands, the DataFrame-in/ProfileDelta-out protocol deserves a brief ADR. Retrospective: cleanest module boundary implementation in the codebase; models.py/profiler.py separation is textbook dependency inversion.

**QA** (FINDING, 2 blockers + 3 advisories fixed):
backlog-compliance PASS. dead-code PASS. reachable-handlers PASS. exception-specificity PASS. silent-failures PASS. coverage-gate FINDING (fixed) — editable install `.pth` pointed to wrong worktree; fixed by re-running `poetry install`; 385 tests, 96.69% coverage. edge-cases FINDING (fixed) — `compare()` misclassified all-null numeric columns as categorical; discriminator changed from `mean is not None` to `is_numeric` flag on `ColumnProfile`; regression test added. error-paths PASS. public-api-coverage PASS. meaningful-asserts PASS. docstring-accuracy FINDING (fixed) — module docstring referenced non-existent class `ProfileReport`; corrected to `TableProfile`. numpy-dep FINDING (fixed) — `numpy` used in tests but not declared; added `numpy>=1.26.0,<3.0.0` to `pyproject.toml`. pandas-stubs-placement FINDING (fixed) — visually ambiguous placement; relocated above integration-group comment. Retrospective: editable install `.pth` pointing to wrong worktree silenced the test suite while lint passed — environment hygiene failure; each worktree must run `poetry install` independently. `compare()` all-null misclassification shows that computed-statistics-as-type-proxy breaks on degenerate inputs — `dtype` or an explicit `is_numeric` flag is the correct discriminator.

**DevOps** (PASS):
hardcoded-credentials PASS. no-pii-in-code PASS. no-auth-material-in-logs PASS. input-validation SKIP (no external inputs). exception-exposure PASS. bandit PASS (0 issues, 3,690 lines). logging-level-appropriate SKIP. dependency-audit PASS (pandas 2.3.3; no CVEs). ci-health PASS. no-speculative-permissions PASS. job-consistency PASS. Advisory: numpy mypy hook lower bound (`>=1.22.0`) is looser than runtime (2.4.3 via pandas); cleanup before Phase 4 integration deps arrive. Retrospective: profiler sets strong precedent — stateless, no I/O, purely synchronous, no infrastructure concerns.

**UI/UX** (SKIP):
Backend-only diff. Forward-looking Phase 5 notes: (1) `ColumnDelta` raw floats need semantic severity tiers at the data layer before Phase 5 dashboard renders them; (2) `value_counts` is unbounded — high-cardinality columns need pagination/top_n hint before template authors see WCAG SC 1.3.1 violations.
---

### [2026-03-14] P4-T4.1 — GPU Passthrough & Ephemeral Storage

**Architecture** (FINDING, fixed):
file-placement PASS — `storage.py` in `modules/synthesizer/` correct per ADR-0017 and CLAUDE.md file placement. `StorageBackend` Protocol is synthesizer-specific; `shared/` not warranted. naming-conventions PASS. dependency-direction PASS — `storage.py` imports only stdlib + third-party (pandas, torch deferred); zero `synth_engine` cross-module imports; import-linter contracts clean. abstraction-level PASS — three-tier stack (Protocol → concrete backend → client) appropriately lean; `InMemoryBackend` correctly in test file. interface-contracts PASS — all public methods fully typed with Google-style docstrings; `type: ignore[no-any-return]` now has inline justification comment; `MinioStorageBackend.get()` docstring documents both raise paths. adr-compliance FINDING (fixed) — CLAUDE.md Rule 8 violation: `EphemeralStorageClient` is an injectable abstraction but no `TODO(T4.2b)` existed in bootstrapper source and no BLOCKER advisory was in RETRO_LOG. Fixed: `TODO(T4.2b)` block added to `bootstrapper/main.py` before `app = create_app()`; ADV-037 BLOCKER row added to Open Advisory Items. Advisory (fixed): `torch` imported at module level — breaks any install without synthesizer group; deferred to inside `_log_device_selection()` body matching the boto3 pattern. Retrospective: Rule 8 compliance gap (TODO in commit message instead of bootstrapper source) has appeared in multiple consecutive tasks — needs mechanical enforcement at plan approval time, not just at review.

**QA** (FINDING, 2 blockers + 4 advisories fixed):
dead-code PASS. reachable-handlers PASS — `MinioStorageBackend.get()` bare `raise` for non-404 ClientErrors is genuinely reachable; `# pragma: no cover` appropriate. exception-specificity PASS. silent-failures PASS. coverage-gate PASS — 338 tests, 96.11% coverage. backlog-compliance FINDING (fixed) — BLOCKER: `TODO(T4.2b)` missing from bootstrapper source; BLOCKER advisory not in RETRO_LOG (CLAUDE.md Rule 8 steps 1 and 2 both absent); both fixed. meaningful-asserts FINDING (fixed) — BLOCKER: `test_force_cpu_logs_info`, `test_gpu_detection_mocked_available`, `test_gpu_detection_mocked_unavailable` all asserted log output only, discarding `_log_device_selection()` return value; all three now capture and assert return value. Advisory A (fixed): `type: ignore[no-any-return]` lacked justification comment. Advisory B (fixed): empty DataFrame round-trip test added. Advisory C (fixed): `MinioStorageBackend.__repr__` added returning redacted string. Advisory D (fixed): `ValueError` guards added to `MinioStorageBackend.__init__` for invalid `endpoint_url` scheme and empty credentials; four tests added. Retrospective: return-value assertions are the primary behavioral contract; log assertions are secondary. Tests of non-void functions must assert return values unless explicitly justified.

**DevOps** (FINDING, 3 blockers + 4 advisories fixed):
hardcoded-credentials PASS. no-pii-in-code PASS. no-auth-material-in-logs PASS — `__repr__` override added; credentials never exposed. input-validation PASS — `ValueError` guards added to `MinioStorageBackend.__init__`. exception-exposure PASS — `KeyError` message contains structural metadata only. bandit PASS — 0 issues. dependency-audit FINDING (fixed) — BLOCKER: `torch >=2.10.0` and `pyarrow >=23.0.0` are non-existent version constraints; `poetry lock` would fail immediately. Corrected to `torch >=2.5.0,<3.0.0` and `pyarrow >=17.0.0,<20.0.0`; `pandas` removed from synthesizer group (already in main group). DevOps BLOCKER 2 (secrets provisioning comments): VERIFIED ALREADY PRESENT in original diff; not a gap. BLOCKER 3 (MinioStorageBackend `__repr__`): fixed. Advisory (read_only, fixed): `minio-ephemeral` service now has `read_only: true` and `/root/.minio tmpfs` consistent with all other hardened services. Advisory (boto3 sync/async): captured as T4.2b Phase-entry gate per ADV-037. Retrospective: aspirational version pins that don't resolve against PyPI break the repo immediately on checkout — all dep pins must be verified before commit.

**UI/UX** (SKIP):
Backend-only diff. Forward: synthesis job lifecycle (queued → uploading → training → generating → done) needs `aria-live="polite"` announcements in Phase 5 dashboard.

---

### [2026-03-14] Phase 3.5 End-of-Phase Retrospective

**Phase:** 3.5 — Technical Debt Sprint ("Back to Solid Ground")
**Tasks completed:** T3.5.0 (process amendments), T3.5.1 (supply chain hardening), T3.5.2 (module cohesion refactor), T3.5.3 (SchemaTopology immutability + VFK), T3.5.4 (bootstrapper wiring + CLI), T3.5.5 (advisory sweep)
**PRs merged:** #20, #21, #22, #23, #24, #25, #26
**Phase status:** ✅ COMPLETE — all 8 exit criteria verified

#### Exit Criteria Audit

| # | Criterion | Result |
|---|-----------|--------|
| 1 | All GitHub Actions SHA-pinned; Trivy job running | ✅ PASS — all `uses:` lines pinned to full SHAs with version comments; `trivy-scan` job green |
| 2 | `modules/mapping/` and `modules/subsetting/` exist; `modules/ingestion/` is clean | ✅ PASS — import-linter 4 contracts kept, 0 broken; `ingestion/` contains only `postgres_adapter.py` + `validators.py` |
| 3 | `SchemaTopology` mutation raises `TypeError`; VFK support tested E2E | ✅ PASS — `MappingProxyType` wrapping verified; VFK integration test in `test_subsetting_integration.py` |
| 4 | `poetry run conclave-subset --help` works; T3.5 E2E test calls CLI via `CliRunner` | ✅ PASS — CLI registered in `pyproject.toml` as `bootstrapper/cli.py:subset`; `test_e2e_subsetting.py` uses `CliRunner` |
| 5 | RETRO_LOG Open Advisory Items table has zero rows (for Phase 3.5 scope) | ✅ PASS — ADV-006/008/025/026/027/028/029/030/031/032/033/034 all drained; ADV-035/036 intentionally deferred to T4.x/T5.1 |
| 6 | All Phase 3.5 tasks have `review(qa):`, `review(arch):`, `review(devops):` commits | ✅ PASS — verified in git log; all 5 substantive tasks have all three review commits |
| 7 | Unit test coverage ≥ 90% | ✅ PASS — 326 tests, 96.95% coverage |
| 8 | Integration tests pass independently | ✅ PASS — CI integration-test job green (CliRunner E2E + VFK integration + ALE + ingestion) |

#### What Went Well

- **Module cohesion refactor (T3.5.2)** delivered cleanly — moving mapping and subsetting out of ingestion resolved the highest-impact architectural debt from Phase 3 with zero test-logic changes required. The import-linter contract expansion locked in the new topology.
- **VFK support (T3.5.3)** was a missing acceptance criterion from T3.2 that had been open since Phase 3. Implementing it as a Phase 3.5 task rather than deferring again was the right call — it will directly unblock Phase 4 profiler work against production databases without physical FK constraints.
- **96.95% unit test coverage** entering Phase 4 is a strong baseline. The coverage gate has held every phase; the 90% floor is credible.
- **`vulture_whitelist.py`** was the right instrument for taming false positives at `--min-confidence 60` without disabling the scan. All 44 entries are manually verified — no blanket suppressions.

#### What Did Not Go Well

- **Three preventable CI failures** occurred during Phase 3.5, all due to known-fixable issues:
  1. `poetry.lock` drift occurred twice (T3.5.1 Dockerfile deps; T3.5.4 click dependency). Pattern: `pyproject.toml` edited, `poetry lock` not run. Fixed by `poetry check --lock` in pre-commit + CI — this gate was added in T3.5.5, not T3.5.1. It should have been added in T3.5.1 when the first drift incident occurred.
  2. Flaky `test_invalid_signature_raises_401` — base64 padding edge case caused non-deterministic failure on Python 3.14. Root cause was a fragile test design (character flip), not a production bug. Fixed by using wrong-key signature. Lesson: tamper tests must be cryptographically guaranteed, not string-manipulation tricks.
  3. `cli.py` placed at package root (outside all import-linter contracts) — this was a planning failure, not a review failure. CLAUDE.md Rule 7 (intra-module cohesion gate at plan approval time) exists specifically to prevent this; the PM did not apply it to T3.5.4 planning.

- **`_load_topology -> Any` latent type bug** (T3.5.4) — function was returning `DirectedAcyclicGraph` when callers expected `SchemaTopology`. This would have caused a runtime `AttributeError` on first real CLI invocation. The pattern: `-> Any` as an escape hatch concealing an unresolved type. Architecture reviewer caught it; but it should have been caught in the RED phase when tests were written against the function signature.

- **Parallel task filesystem contamination** (T3.5.3 / T3.5.4) — both tasks were in flight simultaneously in the same working directory. The T3.5.3 QA reviewer saw false failures from T3.5.4's in-progress files. Worktrees exist for this purpose; they were not used. The PM must enforce worktree isolation for any parallel tasks touching shared files.

#### Process Changes Triggered

- `poetry check --lock` added to pre-commit + CI lint preflight (ADV-006, T3.5.5).
- `no-speculative-permissions` and `job-consistency` checks added to devops-reviewer agent (ADV-032/033, T3.5.5).
- CLAUDE.md Rule 7 (intra-module cohesion gate at plan approval) was in place — it was not applied. PM must explicitly state this check result in future plan approvals.

#### Entering Phase 4 — Known Obligations

| ID | Obligation | Gate |
|----|------------|------|
| ADV-009 | Add `spikes/` to bandit scan targets or document intentional exclusion | Before Phase 4 begins |
| ADV-011 | Document spike-to-production promotion checklist before Phase 4 | Before Phase 4 begins |
| ADV-035 | Wire `MASKING_SALT` from env/Vault into CLI; remove hardcoded fallback | T4.x (masking config task) — **BLOCKER per CLAUDE.md Rule 8** |
| ADV-014 | Pin Dockerfile FROM lines to SHA-256 digests | Before production deployment |
| ADV-021 | Integration tests for `EncryptedString` NULL, empty-string, unicode paths | Before Phase 3/4 TypeDecorator usage grows |

ADV-009 and ADV-011 must be resolved or explicitly deferred with justification before the Phase 4 kickoff plan is approved.

---

### [2026-03-14] P4-T4.3a — OOM Pre-Flight Guardrail

**Architecture** (FINDING, fixed):
file-placement PASS — `guardrails.py` in `modules/synthesizer/` correct per ADR-0017 §T4.3a consequences. naming-conventions PASS — `OOMGuardrailError`, `check_memory_feasibility`, `_available_memory`, `_format_bytes`, `_SAFETY_THRESHOLD` all conform. dependency-direction PASS — `guardrails.py` imports only stdlib (`importlib.util`) + `psutil`; zero `synth_engine` imports; import-linter contracts clean. abstraction-level PASS — single-purpose module; OOM check correctly isolated from synthesis logic. interface-contracts PASS — `check_memory_feasibility` fully typed with Args/Returns/Raises docstring; `OOMGuardrailError` message contract documented. adr-compliance FINDING (fixed) — `psutil` added as production dependency without documenting ADR (CLAUDE.md Rule 6 violation); ADR-0018 created (`docs/adr/ADR-0018-psutil-ram-introspection.md`) evaluating three candidates (`resource` stdlib, `/proc/meminfo` direct read, `psutil`), documenting decision, version range, VRAM fallback path, and air-gap bundling implications. Retrospective: Rule 6 (technology substitution requires ADR) continues to be the most commonly missed process gate. PM should add "grep docs/adr/ for any new production dependency" to the pre-GREEN checklist.

**QA** (FINDING, 2 blockers fixed):
dead-code PASS — `_SAFETY_THRESHOLD` used at guardrails.py line 68; vulture 80% clean. reachable-handlers PASS — `OOMGuardrailError` raise path reachable via `estimated > threshold`. exception-specificity PASS — raises only `OOMGuardrailError` (domain exception) and `ValueError` (input guard). silent-failures PASS — all failure paths raise with human-readable messages. coverage-gate FINDING (fixed) — 86.79% (below 90%) due to shared `.venv` editable install pointing to T4.2a worktree `src/`; profiler files appeared in coverage report at 0%; fixed by `poetry install` in T4.3a branch root; 354 tests, 97.08% coverage after fix. edge-cases FINDING (fixed) — `check_memory_feasibility` lacked guard for non-positive inputs; `ValueError` guards added for `rows≤0`, `columns≤0`, `dtype_bytes≤0`, `overhead_factor≤0.0`; 8 new tests covering zero and negative cases. error-paths, public-api-coverage, meaningful-asserts, docstring-accuracy, backlog-compliance all PASS. Retrospective: shared `.venv` editable install contamination is a recurring pattern (T4.2a and T4.3a both hit it). Each worktree must independently run `poetry install` before any test run — this must be an explicit step in all Phase 4+ software-developer prompts.

**DevOps** (PASS):
hardcoded-credentials PASS. no-pii-in-code PASS. no-auth-material-in-logs PASS. input-validation PASS — `ValueError` guards added for all non-positive inputs. exception-exposure PASS — `OOMGuardrailError` message contains byte counts only; no PII. bandit PASS — 0 issues. dependency-audit PASS — psutil 7.2.2, no CVEs; ADR-0018 documents air-gap implications. ci-health PASS — `psutil` and `types-psutil` added to `mirrors-mypy` `additional_dependencies` in `.pre-commit-config.yaml`. no-speculative-permissions PASS — `psutil.virtual_memory()` is a read-only OS call. Retrospective: bonus pre-commit hook fix (psutil missing from mypy isolated env) caught a latent CI divergence gap — production imports resolving in Poetry venv but failing in pre-commit's isolated mypy env.

**UI/UX** (SKIP):
Backend-only diff. Forward: when `OOMGuardrailError` surfaces in Phase 5 synthesis dashboard, UI must present the `reduction_factor` from the error message as a clear remediation hint with `aria-live` announcement; raw exception strings must not be shown to users.

---

### [2026-03-14] P3.5-T3.5.5 — Advisory Sweep

**Architecture** (PASS, 1 advisory fixed):
file-placement PASS — `masking/luhn.py` lands at the CLAUDE.md canonical location; `vulture_whitelist.py` at project root is correct. naming-conventions PASS. dependency-direction PASS — `algorithms.py` imports intra-module from `masking.luhn`; no cross-module edges introduced; import-linter 4 contracts clean. abstraction-level PASS — `luhn.py` is single-responsibility, 38 lines, zero external deps. interface-contracts PASS — all new public functions have full typed docstrings. adr-compliance PASS — ADR-0014 amended with two-layer salt model (ADV-027). Advisory (fixed): `luhn.py` docstring claimed "synthesizer/privacy modules can import directly from here" — contradicts independence contract; replaced with explicit import boundary note. Drains ADV-006, ADV-008, ADV-025, ADV-026, ADV-027, ADV-029, ADV-030, ADV-032, ADV-033, ADV-034. Retrospective: advisory sweep reflects maturing module boundaries; luhn.py docstring finding is a reminder that docstrings are architectural assertions and must be verified against import-linter contracts; ADV-035 (`_CLI_MASKING_SALT`) must be a Phase 4 entry gate per CLAUDE.md Rule 8.

**QA** (FINDING, 2 blockers + 2 advisories fixed):
coverage-gate PASS (326 tests, 96.95%). dead-code PASS — vulture 80% clean; 60% run all accounted for in `vulture_whitelist.py`. silent-failures PASS — ADV-008 ValueError now logs WARNING. public-api-coverage FINDING (fixed) — ADV-029 AC required "table names AND row counts"; `_written_tables` was `list[str]` with no count tracking; changed to `dict[str, int]`, `write()` accumulates per-table counts, `rollback()` logs both. meaningful-asserts FINDING (fixed) — `test_luhn_check_with_spaces` pre-stripped spaces before calling `luhn_check`, not exercising the function's space-handling; fixed to pass raw `"4111 1111 1111 1111"`. edge-cases advisory (fixed) — `deterministic_hash(length=0)` returned degenerate 0; lower-bound guard added (`length < 1` → `ValueError`) with test. reachable-handlers, exception-specificity, error-paths, docstring-accuracy, type-annotation-accuracy all PASS. Retrospective: ADV-029 gap (row counts vs table names only) is a recurring pattern: multi-part ACs get partially implemented when the test only validates the easier half. The `test_luhn_check_with_spaces` pre-cook pattern is subtle — test inputs must be truly raw, not silently pre-processed.

**DevOps** (PASS):
hardcoded-credentials PASS — gitleaks clean. no-pii-in-code PASS. no-auth-material-in-logs PASS — rollback logs table names (structural metadata, not row content); spike logs column names and row counts (structural). bandit PASS — 0 issues. logging-level-appropriate PASS — spike WARNING for parse error; egress WARNING for Saga rollback both correct. structured-logging PASS — both new loggers use `getLogger(__name__)`. dependency-audit PASS — no new production deps. ci-health PASS — `poetry check --lock` correctly placed after cache restore, before `poetry install`; Poetry 2.2.1 consistent across all jobs. no-speculative-permissions PASS. job-consistency PASS. Forward: when ADV-035 lands (Phase 4 MASKING_SALT wiring), `.env.example` must be updated before that PR merges. Retrospective: systematic observability gap closure — ADV-029 Saga WARNING, ADV-008 spike silent failure, and `poetry check --lock` gate all address the same theme: making failures visible before they become production incidents.

**Phase 3.5 CI Failure Pattern Note:** Three preventable CI failures occurred during Phase 3.5. (1) `poetry.lock` drift — `pyproject.toml` updated without running `poetry lock`, twice (T3.5.1 Dockerfile deps, T3.5.4 click); fixed by `poetry check --lock` in pre-commit + CI (this task). (2) Flaky `test_invalid_signature_raises_401` — base64 padding edge case in JWT tamper test caused non-deterministic failures on Python 3.14; fixed by using a wrong-key signature instead of a last-char flip (T3.5.4). (3) Force-push + concurrent push/PR runs creating duplicate check entries in GitHub; resolved by understanding GitHub's check deduplication behavior.

---

### [2026-03-14] P3.5-T3.5.4 — CLI Entrypoint + Bootstrapper Wiring

**Architecture** (FINDING, 3 fixed + ADR-0016 created):
file-placement FINDING (fixed) — `cli.py` placed at `src/synth_engine/cli.py` (package root) violates CLAUDE.md File Placement Rules ("API Entrypoints → `bootstrapper/`"); moved to `src/synth_engine/bootstrapper/cli.py` via `git mv`. dependency-direction FINDING (fixed) — `synth_engine.cli` was outside all import-linter contracts (governance gap); resolved as a consequence of the move. interface-contracts FINDING (fixed) — `_load_topology() -> Any` concealed a latent type bug: function was returning `DirectedAcyclicGraph` when `SubsettingEngine` expects `SchemaTopology`; fixed by completing the DAG→SchemaTopology conversion inside the function (calls `topological_sort()`, `get_columns()`, `get_foreign_keys()`) and annotating `-> SchemaTopology`. adr-compliance FINDING (fixed) — no ADR for `click` production dependency (CLAUDE.md Rule 6 violation); ADR-0016 created documenting argparse vs click decision, version pin rationale, CliRunner testability advantage, and air-gap safety confirmation. naming-conventions, abstraction-level, model-integrity, no-langchain, async-correctness all PASS. Drains ADV-022 (CycleDetectionError HTTP 422), ADV-028 (SchemaTopology MappingProxyType), ADV-031 (CLI E2E entrypoint). Retrospective: `cli.py` placement gap illustrates that import-linter contracts govern module-to-module boundaries well but leave bootstrapper/wiring layers ungoverned by name; if a third entrypoint emerges (Phase 5 batch scheduler, REPL), revisit whether `bootstrapper/` should be renamed `entrypoints/`. The `_load_topology -> Any` finding masked a real correctness gap — the "orphan Any" pattern is a recurring signal that function contracts were not verified against callers.

**QA** (FINDING, 2 fixed):
coverage-gate PASS (321 tests, 96.91%). dead-code PASS. reachable-handlers PASS. exception-specificity PASS — `except Exception` in `bootstrapper/cli.py` is the justified top-level CLI boundary. silent-failures PASS. edge-cases FINDING (fixed) — `_build_masking_transformer()` lines 100-104 (PII masking path for `persons` table) had zero unit test coverage; two tests added: `test_masking_transformer_masks_pii_columns_for_persons_table` (full PII row, asserts all PII fields changed, non-PII unchanged) and `test_masking_transformer_passthrough_for_none_pii_values` (None-valued PII columns pass through unchanged). docstring-accuracy FINDING (fixed) — `_load_topology()` docstring claimed "A SchemaTopology instance" but function was returning `DirectedAcyclicGraph`; corrected as part of type annotation fix. type-annotation-accuracy FINDING (fixed) — `-> Any` replaced with `-> SchemaTopology`. All 8 AC items verified including CLI CliRunner E2E test. Retrospective: The masking transformer gap is a recurring pattern — closures' actual happy paths (the table that gets masked) are left uncovered while the passthrough path (unknown tables) gets thorough coverage. The docstring inaccuracy on `_load_topology` signals description copied from a higher-level summary rather than verified against the implementation. Private helpers with `-> Any` annotations should trigger a mandatory return-type cross-check before commit.

**DevOps** (PASS):
hardcoded-credentials PASS — gitleaks clean (135 commits); `_CLI_MASKING_SALT` documented as non-secret determinism seed. no-pii-in-code PASS. no-auth-material-in-logs PASS — `bootstrapper/cli.py` has zero logging calls; exception handler emits only sanitized `str(exc)` via `click.echo()`; `_sanitize_url()` strips passwords from DSN error messages; test asserts "Traceback" never appears in output. input-validation PASS — both DSNs validated before engine creation; seed query SELECT-only guard. exception-exposure PASS — RFC 7807 422 body bounded to `type/title/status/detail`; no stack traces in CLI output. bandit PASS — 0 issues; BLE001 suppression for `bootstrapper/cli.py` justified and documented. dependency-audit PASS — click 8.x, no known CVEs, pip-audit clean, pure Python air-gap safe. ci-health PASS. Forward advisories: ADV-035 (`_CLI_MASKING_SALT` hardcoded fallback → T4.x), ADV-036 (`str(exc)` SQLAlchemy frame exposure → T5.1). Retrospective: This diff demonstrates deliberate credential-containment posture — connection strings are treated as opaque operator secrets from intake through error handling, and the test suite explicitly asserts no DSN appears in error output. That guarantee is stronger than most CLIs provide; it should be cited as the reference pattern for the T5.1 HTTP layer.

---

### [2026-03-14] P3.5-T3.5.3 — SchemaTopology Immutability & Virtual FK Support

**Architecture** (PASS, one fix applied):
file-placement PASS. naming-conventions PASS. dependency-direction PASS — reflection.py imports only mapping/graph and SQLAlchemy; schema_topology.py imports only stdlib. abstraction-level PASS — single constructor param + validation-merge pass; no premature generalisation. interface-contracts PASS — keyword-only `virtual_foreign_keys` parameter is good defensive API design; `Mapping[str,...]` annotation correctly describes MappingProxyType runtime type. model-integrity PASS — `object.__setattr__` in `__post_init__` is correct frozen dataclass pattern; `dict(self.columns)` handles re-wrapping edge case. adr-compliance FINDING (fixed) — ADR-0013 §2 had stale VFK deferral language ("separate pass after reflection") contradicting the implemented merge-inside-reflect() design; updated in fix commit. Retrospective: stale ADR sections are the same class of defect as stale code comments; ADR review must be part of the implementation checklist, not an afterthought.

**QA** (PASS, minor fix applied):
All 8 AC items satisfied. 301 tests, 91.25% coverage. VFK edge-cases (None, empty, duplicate, invalid table) all tested. Integration test: real ephemeral PostgreSQL, no physical FK, zero orphaned rows after VFK-driven subsetting. docstring-accuracy FINDING (fixed) — `test_columns_append_raises_type_error` docstring incorrectly described inner `.append()` but tested outer key assignment; corrected in fix commit. Note: two test failures observed during review (`test_commit_is_noop`, `test_context_manager_commits_on_success`) were T3.5.4's in-flight work bleeding into the shared filesystem — confirmed not present on T3.5.3 branch. Retrospective: parallel tasks sharing a working directory is a process risk; review agents should checkout the specific branch before running tests, or parallel tasks should use git worktrees.

**DevOps** (PASS):
gitleaks clean (130 commits). VFK table names validated against reflected schema before any use — correct pattern. VFK column names not SQL-validated (advisory: safe today as Python set keys only; must close if used in query predicates in future). Integration test auth entirely from pytest-postgresql proc fixture. bandit clean. Advisory: if logging is added to reflection.py in future phases, VFK values must not appear in log messages without sanitisation. Drains: ADV-028.

---

### [2026-03-14] P3.5-T3.5.2 — Module Cohesion Refactor

**Architecture** (PASS, one fix applied):
file-placement PASS — all files exactly where backlog spec requires. naming-conventions FINDING (fixed) — `test_subsetting_transversal.py` misspelled; renamed to `test_subsetting_traversal.py`. dependency-direction PASS — mapping imports only sqlalchemy/stdlib; subsetting imports only shared/ (receives SchemaTopology via constructor injection, no import-level dependency on mapping); ingestion does not import from either; no module imports bootstrapper. abstraction-level PASS — bootstrapper-as-value-courier pattern correctly applied. interface-contracts advisory — EgressWriter.commit() no-op is inherited T3.4 debt; explicitly in T3.5.4 scope. adr-compliance PASS — ADR-0013 and ADR-0015 updated; subsetting→mapping exception documented in both. Retrospective: textbook cohesion decomposition; dependency direction is clean; test file naming should receive same rigor as production naming.

**QA** (PASS):
All 6 AC items verified. 287 tests, 97.90% coverage. Vulture 80% clean; 60% produces 10 false positives from `__init__.py` re-export pattern — all confirmed reachable. Edge-cases, error-paths, public-api-coverage, meaningful-asserts all PASS. New advisory ADV-034: add vulture whitelist before false positives mask real findings. Retrospective: test suite is adversarially strong for a refactor ticket; no new debt introduced.

**DevOps** (PASS):
gitleaks clean (124 commits). B608 nosec annotations travel intact through renames (100% similarity) — correct pattern. pyproject.toml changes confined to import-linter contracts only; no new packages; pip-audit clean. Forward advisory: if logging is added to traversal/egress in Phase 4 (both handle raw row data), PIIFilter wiring will be required. Retrospective: import-linter contracts are the right CI leverage point; subsetting→mapping exception is intentionally narrow — watch for scope creep in future PRs.

---

### [2026-03-14] P3.5-T3.5.1 — Supply Chain & CI Hardening

**QA** (PASS):
No Python source changes; all QA checks SKIP. Backlog compliance verified: AC1 — all 7 GitHub Action SHAs independently verified against GitHub API (all match). AC2 — trivy-scan job present with `exit-code: 1` and `severity: HIGH,CRITICAL`; `ignore-unfixed: true` is acceptable noise-reduction. AC3 — `postgresql-16` pinned explicitly; `PG_BIN` hard-coded. AC4 — all 6 pre-existing jobs preserved. Coverage holds at 97.89% (287 passed). Two advisories raised and fixed in this PR: speculative `security-events: write` removed; `snok/install-poetry` version pin added to integration-test job. New advisories logged: ADV-032 (permissions-in-same-commit policy), ADV-033 (cross-job version consistency check). Retrospective: SHA verification documented in commit body is good institutional practice — should be a standing requirement for all future action upgrades. The `security-events: write` pattern (permissions granted before the step that requires them) is a recurring CI smell worth codifying in the devops-reviewer checklist.

**DevOps** (PASS):
All secrets hygiene checks pass. gitleaks clean (115 commits). `.secrets.baseline` correctly removes stale false-positive for removed dynamic PG line. SHA-pinning applied consistently across all 7 actions with inline version comments and update instructions in the file header. Three advisory fixes applied: `pg_ctl --version` validation step added (per spec Context & Constraints); speculative `security-events: write` removed (least-privilege); header comment corrected to show parallel job topology. Drains: ADV-007 (SHA-pin GitHub Actions), ADV-015 (Trivy CI job). Retrospective: permissions must be added in the same commit as the step that requires them — "future use" grants are a recurring blast-radius risk in CI hardening work; adding this as an explicit devops-reviewer checklist item (ADV-032).

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
