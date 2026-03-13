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

---

## Task Reviews

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
