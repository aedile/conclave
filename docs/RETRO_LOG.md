# Conclave Engine — Retrospective Log

Living ledger of review retrospective notes and open advisory items.
Updated after each task's review phase completes.

---

## Open Advisory Items

Advisory findings without a resolved target task are tracked here.
Drain (delete) rows when their target task is completed.

| ID | Source | Target Task | Advisory |
|----|--------|-------------|----------|
| ADV-006 | Arch R2 | Before Task 2.2 | `docs/ARCHITECTURAL_REQUIREMENTS.md` is referenced in `scripts/seed_chroma.py` (SEEDING_MANIFEST) and `docs/adr/ADR-0002` but does not exist in the repo. If absent at runtime, `seed_chroma.py` will `sys.exit(1)` when trying to seed the ADRs collection. Create this file (or update the manifest path) before Phase 2 seeding work begins. |
| ADV-007 | DevOps R1/R3 | Standalone CI hardening task | GitHub Actions in `ci.yml` are pinned to mutable version tags (`@v4`, `@v2`) not commit SHAs. Third-party actions (`gitleaks-action@v2`, `snok/install-poetry`) carry supply-chain risk. SHA-pin all actions in a dedicated CI hardening pass. |
| ADV-008 | QA/DevOps P0.8.1 | Before Task 4.2 (SDV integration) | `_process_chunk()` in `spike_ml_memory.py` uses `except ValueError: pass` — silent swallow must be replaced with `WARNING`-level logging before any synthesizer code is promoted to `src/synth_engine/modules/synthesizer/`. Also: numpy fast path uses unseeded `np.random.normal` (global PRNG state) — breaks determinism; must seed `np.random.default_rng` from same seed as stdlib PRNG before Phase 4 promotion. |
| ADV-009 | QA P0.8.1 | Before Phase 4 | `spikes/` directory is outside bandit and ruff scan targets. As spike code accumulates and patterns are promoted to `src/`, this creates a scan blind spot. Add `spikes/` to bandit targets in `pyproject.toml` or add a `.bandit` marker documenting the intentional exclusion. Also add `# noqa: S311` alongside existing `# nosec B311` at `spike_ml_memory.py` lines 379 and 522. |
| ADV-010 | QA P0.8.2 | Before Phase 3 | `# nosec B311`/`# nosec B608` suppresses bandit only — ruff needs separate `# noqa: S311`/`# noqa: S608` annotations. Four S608 violations exist in `spikes/spike_topological_subset.py`. Fix: add `"spikes/**" = ["S311", "S608"]` to `[tool.ruff.lint.per-file-ignores]` in `pyproject.toml`. This pattern will recur when SQL-adjacent code lands in Phase 3 `src/ingestion/` — apply dual annotations there from the first commit. |
| ADV-011 | QA P0.8.2 | Before Phase 4 (masking module) | `FeistelFPE` in `spike_fpe_luhn.py` has unguarded edge cases: `rounds=0` is an identity transformation (no encryption); `luhn_check("")` and `_luhn_check_digit("")` return `False`/`"0"` silently. Write `tests/unit/test_fpe_luhn.py` (TDD RED) against spike code before promoting to `src/synth_engine/modules/masking/`. Also document spike-to-production promotion checklist in `AUTONOMOUS_DEVELOPMENT_PROMPT.md` before Phase 4. |
| ADV-012 | QA P0.8.3 | Before Phase 3 (ingestion module) | `SubsetQueryGenerator._resolve_reachable()` uses "any-parent OR" semantics to mark a table reachable — correct for downstream-pull subsetting but must be explicitly decided in an ADR before Phase 3 implementation to prevent correctness regressions. Also: `_infer_pk_column()` checks `pk==1` only (incorrect for composite-PK tables). Both must be addressed in the Phase 3 ADR for ingestion subsetting. |
| ADV-013 | DevOps P0.8.3 | Before Phase 3 (ingestion module) | When `SubsetQueryGenerator` is promoted to `src/synth_engine/modules/ingestion/`, `seed_table` crosses a trust boundary. Require allowlist validation against `SchemaInspector.get_tables()` before any f-string SQL construction. Document `spikes/` CI carve-out (no mypy/ruff/bandit enforcement) explicitly in ADR or README so future reviewers do not mistake the absence of enforcement for an oversight. |

---

## Task Reviews

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
